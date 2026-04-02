import { CredentialStore } from "./credential_store";
import { OAuthClient, type TokenResponse } from "./oauth_client";
import { getBackendBaseUrl } from "./backend_url";

function accessTokenExpUnix(accessToken: string): number | null {
  try {
    const parts = accessToken.split(".");
    if (parts.length !== 3) return null;
    const payload = JSON.parse(
      Buffer.from(parts[1]!, "base64url").toString("utf8")
    ) as { exp?: number };
    return payload.exp != null ? Number(payload.exp) : null;
  } catch {
    return null;
  }
}

function tokenPayload(
  token: TokenResponse,
  client?: { clientId: string; clientSecret: string }
): Record<string, string> {
  const out: Record<string, string> = {
    access_token: token.access_token,
    refresh_token: token.refresh_token || "",
  };
  const cid = client?.clientId?.trim() ?? "";
  const sec = client?.clientSecret?.trim() ?? "";
  if (cid) out.client_id = cid;
  if (sec) out.client_secret = sec;
  return out;
}

export class AuthManager {
  private readonly store: CredentialStore;
  private baseUrl: string;

  constructor(credentialStore?: CredentialStore, baseUrl?: string) {
    this.store = credentialStore ?? new CredentialStore();
    this.baseUrl = (baseUrl ?? getBackendBaseUrl()).replace(/\/$/, "");
  }

  async getStorageDescription(): Promise<string> {
    return this.store.getStorageDescription();
  }

  async isLoggedIn(): Promise<boolean> {
    const data = await this.store.load();
    return (
      data != null && Boolean(String(data.access_token || "").trim())
    );
  }

  /** Resolved API base URL from environment (see `getBackendBaseUrl`). */
  getBackendBaseUrl(): string {
    return getBackendBaseUrl();
  }

  async login(clientId: string, clientSecret: string): Promise<void> {
    const url = getBackendBaseUrl().replace(/\/$/, "");
    const client = new OAuthClient(url, clientId, clientSecret);
    const token = await client.requestToken();
    await this.store.save(
      tokenPayload(token, { clientId, clientSecret })
    );
    this.baseUrl = url;
  }

  async logout(): Promise<boolean> {
    return this.store.clear();
  }

  async getValidAccessToken(): Promise<string> {
    const data = await this.store.load();
    if (!data || !String(data.access_token || "").trim()) {
      throw new Error("Not logged in. Run: pipeshub login");
    }
    const access = String(data.access_token);
    const exp = accessTokenExpUnix(access);
    const now = Math.floor(Date.now() / 1000);
    if (exp != null && exp - 60 > now) {
      return access;
    }
    if (exp == null) {
      return access;
    }

    const [baseUrl, clientId, clientSecret] =
      await this.resolveOAuthParamsFromStore();
    const refreshToken = String(data.refresh_token || "").trim();
    const oauth = new OAuthClient(baseUrl, clientId, clientSecret);
    let newToken: TokenResponse;
    if (refreshToken) {
      try {
        newToken = await oauth.refreshAccessToken(refreshToken);
      } catch {
        newToken = await oauth.requestToken();
      }
    } else {
      newToken = await oauth.requestToken();
    }

    const payload = tokenPayload(newToken, {
      clientId,
      clientSecret,
    });
    if (newToken.refresh_token) {
      payload.refresh_token = newToken.refresh_token;
    } else if (refreshToken) {
      payload.refresh_token = refreshToken;
    }
    await this.store.save(payload);
    return newToken.access_token;
  }

  async verifyTokenWithBackend(): Promise<Record<string, unknown>> {
    const data = await this.store.load();
    if (!data || !String(data.access_token || "").trim()) {
      throw new Error("Not logged in. Run: pipeshub login");
    }
    const [baseUrl, clientId, clientSecret] =
      await this.resolveOAuthParamsFromStore();
    const token = await this.getValidAccessToken();
    const oauth = new OAuthClient(baseUrl, clientId, clientSecret);
    return oauth.introspect(token);
  }

  /** client_id / client_secret from keychain or auth.enc (saved at login). */
  private async resolveOAuthParamsFromStore(): Promise<
    [string, string, string]
  > {
    const base = getBackendBaseUrl();
    const stored = await this.store.load();
    const sid = String(stored?.client_id || "").trim();
    const ssec = String(stored?.client_secret || "").trim();
    if (sid && ssec) {
      return [base, sid, ssec];
    }
    throw new Error(
      "Missing OAuth client credentials in secure storage. Run: pipeshub login again."
    );
  }
}
