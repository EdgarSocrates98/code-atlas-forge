"""Testes dos serviços — sem I/O real."""
from app.services import BillingService, UserService


def test_user_lifecycle():
    svc = UserService()
    u = svc.create_user("a@b.c", is_admin=True)
    assert u.id and svc.find_by_email("a@b.c").is_admin


def test_billing_outstanding():
    users, billing = UserService(), BillingService()
    u = users.create_user("x@y.z")
    billing.open_invoice(u, 500)
    billing.open_invoice(u, 700)
    assert billing.outstanding(u) == 1200
