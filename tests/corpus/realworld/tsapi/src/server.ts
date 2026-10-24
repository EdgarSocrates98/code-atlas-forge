import { authenticate, issueSession } from "./auth";
import { listKeys, saveKey } from "./store";

export function login(userId: number): { token: string } {
  return { token: issueSession(userId).token };
}

export function me(token: string): { userId: number } | null {
  const uid = authenticate(token);
  return uid === null ? null : { userId: uid };
}

export function createKey(token: string, label: string) {
  if (authenticate(token) === null) return null;
  const k = { id: listKeys().length + 1, label, scopes: ["read"] };
  saveKey(k);
  return k;
}
