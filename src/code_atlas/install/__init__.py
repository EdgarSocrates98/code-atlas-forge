"""Install lifecycle — provision local environment and record a receipt.

Scope: ``project`` provisions workspace config + repository registration;
``workspace`` discovers member project roots (markers: ``.git`` or a
manifest — gitless members count), registers each under its stable
identity and groups them in a federation workspace; ``user`` provisions
the global data dir only. Every mutation writes a
``forge/InstallReceipt/v1`` under ``<data-dir>/install/`` plus
per-target receipts under ``<target>/.code-atlas/receipts/`` so
``install-status`` and the-forge's delegation scanner can prove what
happened without trusting the caller's memory. Install never indexes.
"""
