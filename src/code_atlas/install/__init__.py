"""Install lifecycle — provision local environment and record a receipt.

Scope: ``project`` provisions workspace config + repository registration;
``user`` provisions the global data dir only. Every mutation writes a
receipt under ``<data-dir>/install/`` so ``install-status`` can prove
what happened without trusting the caller's memory.
"""
