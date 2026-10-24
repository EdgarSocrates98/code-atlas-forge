"""Entrypoint WSGI — roteamento mínimo."""
from urllib.parse import parse_qs

from app.routes import get_outstanding, post_invoice, post_users


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")
    body = environ["wsgi.input"].read()
    if method == "POST" and path == "/users":
        status, payload = post_users(body)
    elif method == "POST" and path == "/invoices":
        status, payload = post_invoice(body)
    elif method == "GET" and path == "/outstanding":
        qs = {k: v[0] for k, v in parse_qs(
            environ.get("QUERY_STRING", "")).items()}
        status, payload = get_outstanding(qs)
    else:
        status, payload = 404, b'{"error": "not found"}'
    start_response(f"{status} X", [("content-type", "application/json")])
    return [payload]
