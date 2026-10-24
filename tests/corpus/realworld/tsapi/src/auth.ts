import { findSession, saveSession } from "./store";
import type { Session } from "./types";

let counter = 0;

export function issueSession(userId: number, ttlMs = 3600_000): Session {
  const s: Session = {
    token: `tok-${++counter}`,
    userId,
    expiresAt: Date.now() + ttlMs,
  };
  saveSession(s);
  return s;
}

export function authenticate(token: string): number | null {
  const s = findSession(token);
  return s ? s.userId : null;
}
