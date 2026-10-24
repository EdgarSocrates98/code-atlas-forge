"""Serviços de negócio — users + billing."""
from app import db
from app.models import Invoice, User


class UserService:
    def create_user(self, email: str, is_admin: bool = False) -> User:
        uid = db.insert("users", {"email": email, "is_admin": is_admin})
        row = db.get("users", uid)
        return User(**row)

    def find_by_email(self, email: str) -> User | None:
        rows = db.where("users", email=email)
        return User(**rows[0]) if rows else None


class BillingService:
    def open_invoice(self, user: User, amount_cents: int) -> Invoice:
        iid = db.insert("invoices",
                        {"user_id": user.id, "amount_cents": amount_cents})
        row = db.get("invoices", iid)
        return Invoice(**row)

    def outstanding(self, user: User) -> int:
        return sum(r["amount_cents"]
                   for r in db.where("invoices", user_id=user.id)
                   if r["status"] == "open")
