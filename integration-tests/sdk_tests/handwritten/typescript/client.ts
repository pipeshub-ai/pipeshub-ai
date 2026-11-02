import { Pipeshub } from "../../index.js";

const SERVER_URL = process.env["PIPESHUB_API_URL"] ?? "http://localhost:3000/api/v1";

export function createClient(): Pipeshub {
  return new Pipeshub({
    serverURL: SERVER_URL,
    security: {
      oauth2: {
        clientID: process.env["PIPESHUB_CLIENT_ID"] ?? "",
        clientSecret: process.env["PIPESHUB_CLIENT_SECRET"] ?? "",
        tokenURL: process.env["PIPESHUB_TOKEN_URL"]
          ?? "http://localhost:3000/api/v1/oauth2/token",
      },
    },
  });
}

/** A client that sends `token` as the bearer: a session JWT or a personal access token. */
export function createBearerClient(token: string): Pipeshub {
  return new Pipeshub({ serverURL: SERVER_URL, security: { bearerAuth: token } });
}

/**
 * Team connectors and OAuth configurations are admin-only, and the OAuth app's tokens act as a
 * service account, which is never an admin.
 */
export function createAdminClient(): Pipeshub {
  return createSessionClient();
}

/** The OAuth app and personal access token routes accept only an interactive session. */
export function createSessionClient(): Pipeshub {
  return createBearerClient(process.env["PIPESHUB_SESSION_TOKEN"] ?? "");
}
