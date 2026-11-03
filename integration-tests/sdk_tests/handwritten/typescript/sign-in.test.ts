import { expect, test } from "vitest";
import { createClient } from "./client.js";

test("Handwritten Sign In", async () => {
  const pipeshub = createClient();
  const email = process.env["PIPESHUB_TEST_USER_EMAIL"] ?? "";

  const init = await pipeshub.userAccount.initAuth({ email });
  expect(init.result.allowedMethods).toContain("password");
  const sessionToken = init.headers["x-session-token"]?.[0];
  expect(sessionToken, "initAuth returned no x-session-token").toBeTruthy();

  const signedIn = await pipeshub.userAccount.authenticate({
    xSessionToken: sessionToken!,
    body: {
      method: "password",
      email,
      credentials: { password: process.env["PIPESHUB_TEST_USER_PASSWORD"] ?? "" },
    },
  });
  if (!("refreshToken" in signedIn)) {
    throw new Error(`sign-in did not finish: ${JSON.stringify(signedIn)}`);
  }
  expect(signedIn.accessToken).toBeTruthy();

  const refreshed = await pipeshub.userAccount.refreshToken({
    scopedToken: signedIn.refreshToken,
  });
  expect(refreshed.accessToken).toBeTruthy();
  expect(refreshed.user.email).toEqual(email);
});
