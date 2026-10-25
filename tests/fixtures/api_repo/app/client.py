"""Python HTTP client fixture: literal + dynamic consumer calls."""

import httpx
import requests
import urllib.request

BASE = "https://api.pets.example"


def fetch_pets():
    r = requests.get("https://api.pets.example/v1/pets")
    requests.post("/v1/pets")
    return r


def fetch_dynamic():
    url = f"{BASE}/pets/9"
    return requests.get(url)


httpx.request("DELETE", "https://api.pets.example/v1/pets/1")
urllib.request.urlopen("https://other.example/ping")
