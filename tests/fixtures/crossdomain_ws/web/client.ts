// Consumidor TS do workspace cross-domain (lf-0124).
// fetch("/v1/pets") sem método declarado → candidatos GET+POST do
// contrato → ambiguous honesto. URL absoluta externa → external.
export async function loadPets() {
  const res = await fetch("/v1/pets");
  return res.json();
}

fetch("https://cdn.test.example/lib.js");
