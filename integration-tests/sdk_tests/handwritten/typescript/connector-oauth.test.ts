import { randomUUID } from "node:crypto";
import { expect, test } from "vitest";
import { createClient } from "./client.js";

// Completing the flow needs a person to consent in a browser, so this stops at the consent URL.
test("Handwritten Connector OAuth Authorization", async () => {
  const pipeshub = createClient();
  // OAuth app and connector names are unique in an org.
  const name = `sdk-test-jira-${randomUUID()}`;

  const config = await pipeshub.oAuthConfiguration.createOAuthConfig({
    connectorType: "Jira",
    body: {
      oauthInstanceName: name,
      config: { clientId: "sdk-test-client-id", clientSecret: "sdk-test-client-secret" },
    },
  });
  const configId = config.oauthConfig.id!;
  try {
    const created = await pipeshub.connectorInstances.createConnectorInstance({
      connectorType: "Jira",
      instanceName: name,
      scope: "team",
      authType: "OAUTH",
      config: { auth: { oauthConfigId: configId } },
    });
    const connectorId = created.connector.connectorId;
    try {
      const authorization = await pipeshub.connectorOAuth.getOAuthAuthorizationUrl({
        connectorId,
      });
      const url = new URL(authorization.authorizationUrl);
      expect(url.origin).toEqual("https://auth.atlassian.com");
      expect(url.searchParams.get("client_id")).toEqual("sdk-test-client-id");
      expect(authorization.state).toBeTruthy();
    } finally {
      await pipeshub.connectorInstances.deleteConnectorInstance({ connectorId });
    }
  } finally {
    await pipeshub.oAuthConfiguration.deleteOAuthConfig({ connectorType: "Jira", configId });
  }
});
