import { AuthManager } from "../auth/auth_manager";
import { BackendClient } from "../api/backend_client";

export async function backendBase(manager: AuthManager): Promise<string> {
  const stored = await manager.getStoredBaseUrl();
  if (stored) return stored.replace(/\/$/, "");
  return (process.env.PIPESHUB_BACKEND_URL || "http://localhost:3000").replace(
    /\/$/,
    ""
  );
}

export async function createBackendClient(manager: AuthManager): Promise<{
  api: BackendClient;
  base: string;
}> {
  const token = await manager.getValidAccessToken();
  const base = await backendBase(manager);
  return { api: new BackendClient(base, token), base };
}
