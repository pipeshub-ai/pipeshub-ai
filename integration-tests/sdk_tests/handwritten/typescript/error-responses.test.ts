import { test } from "vitest";
import { createBearerClient, createClient, createSessionClient } from "./client.js";
import { expectStatus } from "./errors.js";

// IDs no record, connector, agent or conversation has.
const NO_UUID = "00000000-0000-4000-8000-000000000000";
const NO_OBJECT_ID = "000000000000000000000000";

test("Handwritten Error Responses", async () => {
  const pipeshub = createClient();

  await expectStatus(pipeshub.conversations.getConversationById({ conversationId: NO_OBJECT_ID }), 404);
  await expectStatus(pipeshub.conversations.pinConversation({ conversationId: NO_OBJECT_ID }), 404);
  await expectStatus(
    pipeshub.conversations.addMessage({ conversationId: NO_OBJECT_ID, body: { query: "sdk-test" } }),
    404,
  );
  await expectStatus(
    pipeshub.conversations.forkConversation({ conversationId: NO_OBJECT_ID, messageId: NO_OBJECT_ID }),
    404,
  );
  await expectStatus(
    pipeshub.conversations.cancelConversationStream({
      conversationId: NO_OBJECT_ID,
      body: { runId: "sdk-test-not-a-uuid" },
    }),
    400,
  );
  await expectStatus(pipeshub.agents.getAgent({ agentKey: NO_UUID }), 404);

  // DELETE /search ignores its search filter and deletes all of the user's history, which the
  // other suites share, so only a request it refuses before deleting anything is sent.
  await expectStatus(
    pipeshub.semanticSearch.deleteSearchHistory({ search: "<script>sdk-test</script>" }),
    400,
  );

  await expectStatus(pipeshub.knowledgeBase.getRecordById({ recordId: NO_UUID }), 404);
  await expectStatus(pipeshub.connector.getRecordContent({ recordId: NO_UUID }), 403);
  // ENABLE_SOFT_DELETE is off on the CI app (the default), so the trash refuses.
  await expectStatus(pipeshub.knowledgeBase.listTrash({ kbId: NO_UUID }), 403);
  await expectStatus(pipeshub.knowledgeBase.restoreRecord({ recordId: NO_UUID }), 403);
  await expectStatus(
    pipeshub.knowledgeBase.reindexRecordGroup({ recordGroupId: NO_UUID, body: { depth: 0 } }),
    404,
  );
  await expectStatus(
    pipeshub.knowledgeHub.getKnowledgeHubChildNodes({ parentType: "recordGroup", parentId: NO_UUID }),
    404,
  );
  await expectStatus(pipeshub.connector.navigateKnowledgeGraph({ limit: 10 }), 400);

  await expectStatus(
    pipeshub.connectorRegistry.getConnectorSchema({ connectorType: "sdk-test-no-such-type" }),
    404,
  );
  await expectStatus(
    pipeshub.connectorRegistry.testConnectorConnection({ connectorType: "RSS", body: { auth: {} } }),
    400,
  );
  await expectStatus(pipeshub.connectorInstances.getConnectorInstance({ connectorId: NO_UUID }), 404);
  await expectStatus(pipeshub.connectorInstances.deleteConnectorInstance({ connectorId: NO_UUID }), 404);
  await expectStatus(pipeshub.oAuthConfiguration.getOAuthConnectorType({ connectorType: "RSS" }), 404);
  await expectStatus(
    pipeshub.oAuthConfiguration.getOAuthConfig({ connectorType: "Jira", configId: NO_UUID }),
    404,
  );

  await expectStatus(
    pipeshub.oAuthProvider.oauthToken({
      grantType: "client_credentials",
      clientId: process.env["PIPESHUB_CLIENT_ID"] ?? "",
      clientSecret: "sdk-test-wrong-secret",
    }),
    401,
  );
});

test("Handwritten Session Error Responses", async () => {
  const session = createSessionClient();

  await expectStatus(session.oAuthApps.getOAuthApp({ appId: NO_OBJECT_ID }), 404);
  await expectStatus(
    session.personalAccessTokens.revokePersonalAccessToken({ tokenId: NO_OBJECT_ID }),
    404,
  );
  await expectStatus(
    session.personalAccessTokens.adminRevokePersonalAccessToken({ tokenId: NO_OBJECT_ID }),
    404,
  );
  await expectStatus(
    session.personalAccessTokens.createPersonalAccessToken({
      name: "sdk-test-pat-bad-scope",
      scopes: ["sdk-test:no-such-scope"],
    }),
    400,
  );
  // userinfo needs a token that carries `openid`; a session JWT carries no scopes.
  await expectStatus(session.openIDConnect.oauthUserInfo(), 401);
  // Only bodies the server refuses: a real change would alter how the other suites sign in.
  await expectStatus(session.organizationAuthConfig.updateAuthMethod({ authMethod: [] }), 400);
  await expectStatus(
    session.organizationAuthConfig.updateAuthMethod({
      authMethod: [{ order: 1, allowedMethods: [{ type: "password" }, { type: "password" }] }],
    }),
    400,
  );
  await expectStatus(
    session.userAccount.resetPassword({
      currentPassword: "sdk-test-wrong-current-password",
      newPassword: "Sdk-test-unused-password-1",
    }),
    401,
  );

  await expectStatus(session.userAccount.initAuth({ email: "sdk-test-not-an-email" }), 400);
  await expectStatus(
    session.userAccount.authenticate({
      xSessionToken: "sdk-test-invalid-session",
      body: { method: "password", credentials: { password: "sdk-test-wrong-password" } },
    }),
    401,
  );
  await expectStatus(
    session.userAccount.refreshToken({ scopedToken: "sdk-test-invalid-refresh-token" }),
    401,
  );

  // A personal access token works on the API but not on the routes that manage tokens and apps.
  const created = await session.personalAccessTokens.createPersonalAccessToken({
    name: "sdk-test-pat-refused",
    scopes: ["openid", "kb:read"],
    expiryDays: 30,
  });
  const tokenId = created.token.id;
  try {
    const pat = createBearerClient(created.token.accessToken);
    await expectStatus(pat.oAuthApps.listOAuthApps(), 403);
    await expectStatus(pat.personalAccessTokens.createPersonalAccessToken({ name: "sdk-test" }), 403);
  } finally {
    await session.personalAccessTokens.revokePersonalAccessToken({ tokenId });
  }
  await expectStatus(createBearerClient(created.token.accessToken).openIDConnect.oauthUserInfo(), 401);
});
