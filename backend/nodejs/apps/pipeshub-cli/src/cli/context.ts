import { AuthManager } from "../auth/auth_manager";
import { BackendClient } from "../api/backend_client";
import { getBackendBaseUrl } from "../auth/backend_url";

export function backendBase(): string {
  return getBackendBaseUrl();
}

export async function createBackendClient(manager: AuthManager): Promise<{
  api: BackendClient;
  base: string;
}> {
  const token = await manager.getValidAccessToken();
  const base = getBackendBaseUrl();
  return { api: new BackendClient(base, token), base };
}
