"""Rotas HTTP — validação + dispatch."""
import json

from app.services import BillingService, UserService

_USERS = UserService()
_BILLING = BillingService()


def post_users(body: bytes) -> tuple[int, bytes]:
    data = json.loads(body or b"{}")
    if "email" not in data:
        return 422, b'{"error": "email required"}'
    user = _USERS.create_user(data["email"], bool(data.get("is_admin")))
    return 201, json.dumps({"id": user.id}).encode()


def post_invoice(body: bytes) -> tuple[int, bytes]:
    data = json.loads(body or b"{}")
    user = _USERS.find_by_email(data.get("email", ""))
    if user is None:
        return 404, b'{"error": "user not found"}'
    inv = _BILLING.open_invoice(user, int(data["amount_cents"]))
    return 201, json.dumps({"id": inv.id}).encode()


def get_outstanding(query: dict) -> tuple[int, bytes]:
    user = _USERS.find_by_email(query.get("email", ""))
    if user is None:
        return 404, b'{"error": "user not found"}'
    return 200, json.dumps(
        {"cents": _BILLING.outstanding(user)}).encode()
