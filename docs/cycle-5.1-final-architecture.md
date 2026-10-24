# Cycle 5.1 — Arquitetura Final

```
ImpactRequest
 → ChangeSet (git plumbing read-only, --numstat p/ binary)
 → ImpactSnapshotResolver → ResolvedImpactSnapshots (repo-aware)
 → revision_binding.plan → ImpactRevisionPlan (hard invariant)
     MISMATCH/UNAVAILABLE → resultado Git-only + semantic-blocked
 → symbol_diff + _pair_by_hash (1:1 verified, N:M ambíguo)
 → blast_radius × {target, base}  (multi-origem: visited=(occ,orig))
 → _reconcile → consumer_state (still_present|consumer_modified|
     also_removed|unresolved_in_target)
 → contract_analyzer → test_selector(target+base, revision-marked)
 → risk_classifier → report/packs (mesmos diagnósticos)
```

Invariantes: isolamento por repositório; revisão⇄snapshot comprovável
ou degradação explícita; nenhuma execução de código indexado.
