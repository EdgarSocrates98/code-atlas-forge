"""Chamadas HTTP cliente em código — consumidores de endpoints (lf-0114).

Somente **literais estáticos** viram URL: ``fetch("/x")``,
``requests.get("http://a/b")``, ``axios.post(...)``. Qualquer forma
dinâmica (f-string, template ``${}``, variável, concatenação, objeto de
config sem ``url`` literal) vira marcador ``dynamic:<expr>`` com status
``unresolved`` — nunca resolvido, nunca inventado.

Vínculo literal-URL ↔ contrato/endpoint local é da Phase 9
(cross-domain resolution): aqui emitimos ``CALLS_ENDPOINT`` com status
``unresolved`` (URL relativa) ou ``external`` (URL absoluta
``http(s)://`` fora do snapshot) + ``candidates`` com endpoints locais
cujo ``full_path`` casa textualmente — dado de pista, não resolução.

Spans/callees vêm do ``ParseResult`` (AST real) para .py/.ts; ``.js`` usa
regex-static por linha (``heuristic``). O método HTTP vai em ``notes``
da evidência (``method=POST``) quando declarado literalmente.
"""

from __future__ import annotations

import re

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains.api import common
from code_atlas.domains.api.emit import Emit
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainRelationKind
from code_atlas.parsers.contracts import ParseResult, SourceSpan

# callee → índice do argumento que carrega a URL.
PY_URL_ARG: dict[str, int] = {
    "requests.get": 0, "requests.post": 0, "requests.put": 0,
    "requests.delete": 0, "requests.patch": 0, "requests.head": 0,
    "requests.options": 0, "requests.request": 1,
    "httpx.get": 0, "httpx.post": 0, "httpx.put": 0,
    "httpx.delete": 0, "httpx.patch": 0, "httpx.head": 0,
    "httpx.options": 0, "httpx.request": 1,
    "urllib.request.urlopen": 0,
}
TS_URL_ARG: dict[str, int] = {
    "fetch": 0, "axios": 0, "axios.request": 0,
    "axios.get": 0, "axios.post": 0, "axios.put": 0,
    "axios.delete": 0, "axios.patch": 0, "axios.head": 0,
    "axios.options": 0,
}
_AXIOS_URL_PROP = re.compile(r"\burl\s*:\s*('([^']*)'|\"([^\"]*)\")")
_METHOD_PROP = re.compile(r"\bmethod\s*:\s*['\"](\w+)['\"]")
_JS_CALL = re.compile(r"\b(fetch|axios(?:\.\w+)?)\s*\(")


def _method_note(callee: str, args: list[str], url_idx: int) -> str:
    """Método HTTP declarado literalmente — vai em ``evidence.notes``."""
    method = ""
    if url_idx == 1 and args:  # requests.request("GET", url)
        lit = common.parse_str_literal(args[0], lang="py")
        if lit and not lit.dynamic:
            method = lit.value.upper()
    elif callee == "fetch" and len(args) > 1:
        m = _METHOD_PROP.search(args[1])
        if m:
            method = m.group(1).upper()
    elif callee.startswith("axios."):
        method = callee.rsplit(".", 1)[-1].upper()
        if method == "REQUEST":
            method = ""
    elif callee.startswith(("requests.", "httpx.")):
        method = callee.rsplit(".", 1)[-1].upper()
    return f"method={method}" if method else ""


def _candidates(sink: Emit, url: str) -> tuple[DomainIdentity, ...]:
    """Endpoints locais cujo path casa — pista para Phase 9, não resolução."""
    path = common.url_path(url)
    found: list[DomainIdentity] = []
    seen: set[str] = set()
    for ident in sink.endpoint_paths.get(path, ()):
        key = ident.logical_key()
        if key not in seen:
            seen.add(key)
            found.append(ident)
    return tuple(found)


def emit_call(
    *,
    sink: Emit,
    path: str,
    caller: str,
    callee: str,
    args_text: str | None,
    url_idx: int,
    span: SourceSpan | None,
    content_hash: str,
    confidence: ConfidenceClass,
    lang: str,
) -> None:
    """Uma call-site HTTP → ``CALLS_ENDPOINT`` com estado honesto."""
    args = common.split_args(args_text or "")
    if len(args) <= url_idx:
        sink.diag(
            "call-args-unparsed", "info",
            f"{callee}(...) without readable args at {path}",
            path,
        )
        return
    token = args[url_idx]
    # axios({url: "/x", method: "post"}) — objeto de config com url literal.
    if token.startswith("{") and callee.startswith("axios"):
        m = _AXIOS_URL_PROP.search(token)
        if m:
            token = m.group(1)
    lit = common.parse_str_literal(token, lang=lang)
    notes = _method_note(callee, args, url_idx)
    evi = sink.evi(path, content_hash, span, notes)

    if lit is None or lit.dynamic:
        marker = (lit.value if lit else token).strip()[:80]
        sink.relation(
            relation_type=DomainRelationKind.CALLS_ENDPOINT,
            path=path,
            from_symbol=caller,
            to_unresolved=f"dynamic:{marker}",
            resolution=sink.resolution(
                ResolutionStatus.UNRESOLVED, ConfidenceClass.HEURISTIC,
                unresolved_text=f"dynamic:{marker}",
            ),
            span=span,
            evidence=(evi,),
        )
        sink.diag(
            "dynamic-url", "info",
            f"{callee} call with dynamic URL marker — never resolved "
            f"({marker[:60]!r})",
            path,
        )
        sink.bump("consumer_calls_dynamic")
        return

    url = lit.value
    if not url.strip():
        sink.diag(
            "empty-url", "warning",
            f"{callee} call with empty URL literal", path,
        )
        return
    candidates = _candidates(sink, url)
    if common.is_absolute_url(url):
        status = ResolutionStatus.EXTERNAL
    else:
        status = ResolutionStatus.UNRESOLVED
    sink.relation(
        relation_type=DomainRelationKind.CALLS_ENDPOINT,
        path=path,
        from_symbol=caller,
        to_unresolved=url,
        resolution=sink.resolution(
            status, confidence,
            candidates=candidates,
            unresolved_text=url,
        ),
        span=span,
        evidence=(evi,),
    )
    sink.bump("consumer_calls")


def extract_consumers(
    *,
    sink: Emit,
    path: str,
    text: str,
    result: ParseResult,
    module: str,
    content_hash: str,
    table: dict[str, int],
    lang: str,
    confidence: ConfidenceClass,
) -> None:
    """Call-sites HTTP a partir de refs ``call`` do AST real."""
    for ref in result.references:
        if ref.context != "call":
            continue
        callee = ref.name.strip()
        url_idx = table.get(callee)
        if url_idx is None:
            continue
        scope = ".".join(ref.scope_path)
        caller = f"{module}.{scope}" if scope else module
        emit_call(
            sink=sink, path=path, caller=caller, callee=callee,
            args_text=common.args_after(
                text, common.byte_to_char(text, ref.span.end_byte)
            ),
            url_idx=url_idx, span=ref.span,
            content_hash=content_hash,
            confidence=confidence, lang=lang,
        )


def extract_js_consumers(
    *,
    sink: Emit,
    path: str,
    text: str,
    module: str,
    content_hash: str,
) -> None:
    """Regex-static por linha para .js — sem AST; ``heuristic``."""
    byte_offs = common.byte_offsets(text)
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in _JS_CALL.finditer(line):
            callee = m.group(1)
            url_idx = TS_URL_ARG.get(callee)
            if url_idx is None:
                continue
            span = common.line_span(text, byte_offs, lineno)
            emit_call(
                sink=sink, path=path, caller=module, callee=callee,
                args_text=common.scan_args(line, m.end() - 1),
                url_idx=url_idx, span=span,
                content_hash=content_hash,
                confidence=ConfidenceClass.HEURISTIC, lang="ts",
            )
