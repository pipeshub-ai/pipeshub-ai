const DEFAULT_TOKEN_PATH = "/api/v1/oauth2/token";
const DEFAULT_INTROSPECT_PATH = "/api/v1/oauth2/introspect";

export type TokenResponse = {
  access_token: string;
  token_type: string;
  expires_in?: number;
  refresh_token?: string;
  scope?: string;
  raw: Record<string, unknown>;
  issued_at: number;
};

async function raiseForStatus(resp: Response): Promise<void> {
  if (resp.status < 400) return;
  let body = "";
  try {
    body = (await resp.text()).slice(0, 800);
  } catch {
    /* ignore */
  }
  let msg = `${resp.status}, url=${resp.url}`;
  if (body.trim()) msg += ` — ${body.trim()}`;
  throw new Error(msg);
}

function parseTokenResponse(data: Record<string, unknown>): TokenResponse {
  const access = data.access_token;
  if (typeof access !== "string" || !access) {
    throw new Error("Token response missing access_token");
  }
  return {
    access_token: access,
    token_type: typeof data.token_type === "string" ? data.token_type : "Bearer",
    expires_in: typeof data.expires_in === "number" ? data.expires_in : undefined,
    refresh_token:
      typeof data.refresh_token === "string" ? data.refresh_token : undefined,
    scope: typeof data.scope === "string" ? data.scope : undefined,
    raw: data,
    issued_at: Math.floor(Date.now() / 1000),
  };
}

export class OAuthClient {
  readonly baseUrl: string;
  readonly clientId: string;
  readonly clientSecret: string;
  readonly tokenUrl: string;
  readonly introspectUrl: string;

  constructor(
    baseUrl: string,
    clientId: string,
    clientSecret: string,
    tokenPath?: string
  ) {
    const path =
      tokenPath ?? process.env.PIPESHUB_OAUTH_TOKEN_PATH ?? DEFAULT_TOKEN_PATH;
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.clientId = clientId;
    this.clientSecret = clientSecret;
    this.tokenUrl = this.baseUrl + path;
    this.introspectUrl =
      this.baseUrl +
      (process.env.PIPESHUB_OAUTH_INTROSPECT_PATH ?? DEFAULT_INTROSPECT_PATH);
  }

  async requestToken(): Promise<TokenResponse> {
    const payload = new URLSearchParams({
      grant_type: "client_credentials",
      client_id: this.clientId,
      client_secret: this.clientSecret,
    });
    const resp = await fetch(this.tokenUrl, {
      method: "POST",
      body: payload,
      headers: { Accept: "application/json" },
    });
    await raiseForStatus(resp);
    const data = (await resp.json()) as Record<string, unknown>;
    return parseTokenResponse(data);
  }

  async refreshAccessToken(refreshToken: string): Promise<TokenResponse> {
    const payload = new URLSearchParams({
      grant_type: "refresh_token",
      client_id: this.clientId,
      client_secret: this.clientSecret,
      refresh_token: refreshToken,
    });
    const resp = await fetch(this.tokenUrl, {
      method: "POST",
      body: payload,
      headers: { Accept: "application/json" },
    });
    await raiseForStatus(resp);
    const data = (await resp.json()) as Record<string, unknown>;
    return parseTokenResponse(data);
  }

  async introspect(accessToken: string): Promise<Record<string, unknown>> {
    const payload = new URLSearchParams({
      token: accessToken,
      client_id: this.clientId,
      client_secret: this.clientSecret,
    });
    const resp = await fetch(this.introspectUrl, {
      method: "POST",
      body: payload,
      headers: { Accept: "application/json" },
    });
    await raiseForStatus(resp);
    return (await resp.json()) as Record<string, unknown>;
  }
}
