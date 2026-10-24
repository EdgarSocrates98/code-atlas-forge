import type { ApiKey, Session } from "./types";

const sessions = new Map<string, Session>();
const keys = new Map<number, ApiKey>();

export function saveSession(s: Session): void {
  sessions.set(s.token, s);
}

export function findSession(token: string): Session | undefined {
  const s = sessions.get(token);
  return s && s.expiresAt > Date.now() ? s : undefined;
}

export function saveKey(k: ApiKey): void {
  keys.set(k.id, k);
}

export function listKeys(): ApiKey[] {
  return [...keys.values()];
}
