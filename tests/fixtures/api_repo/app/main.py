"""FastAPI fixture app: app + APIRouter + include_router + dynamic route."""

from fastapi import APIRouter, FastAPI

from routers import pets_router

app = FastAPI(root_path="/edge")

admin = APIRouter(prefix="/admin")


@app.get("/health")
def health():
    return {"ok": True}


@admin.post("/users/{uid}")
def create_user(uid: str):
    return {"uid": uid}


@app.get(f"/{dyn}")
def dynamic_route(dyn: str):
    return {"dyn": dyn}


@app.websocket("/ws")
async def ws():
    return None


app.include_router(admin, prefix="/v2")
app.include_router(pets_router, prefix="/api")
