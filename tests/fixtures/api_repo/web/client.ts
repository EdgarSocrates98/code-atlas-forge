export async function loadPets() {
  const res = await fetch("https://api.pets.example/v1/pets");
  const one = await fetch("/v1/pets/1");
  return res.json() + one.status;
}

const cfg = { base: "https://x.example" };

fetch(`${cfg.base}/dyn`);

axios.post("https://api.pets.example/v1/pets", { name: "rex" });
axios.request({ url: "/v1/pets/2", method: "get" });
