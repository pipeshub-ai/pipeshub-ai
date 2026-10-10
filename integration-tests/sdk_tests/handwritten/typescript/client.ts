import { Pipeshub } from "../../index.js";

export function createClient(): Pipeshub {
  return new Pipeshub({
    serverURL: process.env["PIPESHUB_API_URL"] ?? "http://localhost:3000/api/v1",
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
