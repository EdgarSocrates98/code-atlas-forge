"""APIRouter module — router declared and used in the same file."""

from fastapi import APIRouter

pets_router = APIRouter(prefix="/pets")


@pets_router.get("/")
def list_pets():
    return []


@pets_router.get("/{pet_id}")
def get_pet(pet_id: int):
    return {"id": pet_id}
