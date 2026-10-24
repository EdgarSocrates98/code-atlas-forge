export interface Session {
  token: string;
  userId: number;
  expiresAt: number;
}

export interface ApiKey {
  id: number;
  label: string;
  scopes: string[];
}
