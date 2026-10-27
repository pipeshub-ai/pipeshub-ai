package handwritten

import (
	"context"
	"testing"

	pipeshub "github.com/pipeshub-ai/pipeshub-sdk-go"
	"github.com/pipeshub-ai/pipeshub-sdk-go/internal/utils"
	"github.com/pipeshub-ai/pipeshub-sdk-go/models/components"
	"github.com/pipeshub-ai/pipeshub-sdk-go/models/operations"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// IDs no record, connector, agent or conversation has.
const (
	noUUID     = "00000000-0000-4000-8000-000000000000"
	noObjectID = "000000000000000000000000"
)

func TestHandwritten_ErrorResponses(t *testing.T) {
	ctx := context.Background()
	s := newClient()
	var err error

	_, err = s.Conversations.GetConversationByID(ctx, operations.GetConversationByIDRequest{ConversationID: noObjectID})
	requireStatus(t, 404, err)
	_, err = s.Conversations.PinConversation(ctx, noObjectID)
	requireStatus(t, 404, err)
	_, err = s.Conversations.AddMessage(ctx, noObjectID, components.AddMessageRequest{Query: "sdk-test"})
	requireStatus(t, 404, err)
	_, err = s.Conversations.ForkConversation(ctx, noObjectID, noObjectID)
	requireStatus(t, 404, err)
	_, err = s.Conversations.CancelConversationStream(ctx, noObjectID, operations.CancelConversationStreamRequestBody{
		RunID: "sdk-test-not-a-uuid",
	})
	requireStatus(t, 400, err)
	_, err = s.Agents.GetAgent(ctx, noUUID)
	requireStatus(t, 404, err)

	// DELETE /search ignores its search filter and deletes all of the user's history, which the
	// other suites share, so only a request it refuses before deleting anything is sent.
	_, err = s.SemanticSearch.DeleteSearchHistory(ctx, pipeshub.Pointer("<script>sdk-test</script>"), nil, nil, nil)
	requireStatus(t, 400, err)

	_, err = s.KnowledgeBase.GetRecordByID(ctx, noUUID, nil, nil)
	requireStatus(t, 404, err)
	_, err = s.Connector.GetRecordContent(ctx, operations.GetRecordContentRequest{RecordID: noUUID})
	requireStatus(t, 403, err)
	// ENABLE_SOFT_DELETE is off on the CI app (the default), so the trash refuses.
	_, err = s.KnowledgeBase.ListTrash(ctx, noUUID, nil, nil)
	requireStatus(t, 403, err)
	_, err = s.KnowledgeBase.RestoreRecord(ctx, noUUID)
	requireStatus(t, 403, err)
	_, err = s.KnowledgeBase.ReindexRecordGroup(ctx, noUUID, &components.ReindexRecordGroupRequestBody{
		Depth: pipeshub.Pointer[int64](0),
	})
	requireStatus(t, 404, err)
	_, err = s.KnowledgeHub.GetKnowledgeHubChildNodes(ctx, operations.GetKnowledgeHubChildNodesRequest{
		ParentType: operations.ParentTypeRecordGroup,
		ParentID:   noUUID,
	})
	requireStatus(t, 404, err)
	_, err = s.Connector.NavigateKnowledgeGraph(ctx, operations.NavigateKnowledgeGraphRequest{Limit: pipeshub.Pointer[int64](10)})
	requireStatus(t, 400, err)

	_, err = s.ConnectorRegistry.GetConnectorSchema(ctx, "sdk-test-no-such-type")
	requireStatus(t, 404, err)
	_, err = s.ConnectorRegistry.TestConnectorConnection(ctx, "RSS", operations.TestConnectorConnectionRequestBody{
		Auth: map[string]any{},
	})
	requireStatus(t, 400, err)
	_, err = s.ConnectorInstances.GetConnectorInstance(ctx, noUUID)
	requireStatus(t, 404, err)
	_, err = s.ConnectorInstances.DeleteConnectorInstance(ctx, noUUID)
	requireStatus(t, 404, err)
	_, err = s.OAuthConfiguration.GetOAuthConnectorType(ctx, "RSS", nil, nil, nil)
	requireStatus(t, 404, err)
	_, err = s.OAuthConfiguration.GetOAuthConfig(ctx, "Jira", noUUID, nil)
	requireStatus(t, 404, err)

	_, err = s.OAuthProvider.OauthToken(ctx, components.OAuthTokenRequest{
		GrantType:    components.GrantTypeClientCredentials,
		ClientID:     pipeshub.Pointer(utils.GetEnv("PIPESHUB_CLIENT_ID", "")),
		ClientSecret: pipeshub.Pointer("sdk-test-wrong-secret"),
	})
	requireStatus(t, 401, err)
}

func TestHandwritten_SessionErrorResponses(t *testing.T) {
	ctx := context.Background()
	session := newSessionClient()
	var err error

	_, err = session.OAuthApps.GetOAuthApp(ctx, noObjectID)
	requireStatus(t, 404, err)
	_, err = session.PersonalAccessTokens.RevokePersonalAccessToken(ctx, noObjectID, nil)
	requireStatus(t, 404, err)
	_, err = session.PersonalAccessTokens.AdminRevokePersonalAccessToken(ctx, noObjectID, nil)
	requireStatus(t, 404, err)
	_, err = session.PersonalAccessTokens.CreatePersonalAccessToken(ctx, components.CreatePatRequest{
		Name:   "sdk-test-pat-bad-scope",
		Scopes: []string{"sdk-test:no-such-scope"},
	})
	requireStatus(t, 400, err)
	// userinfo needs a token that carries `openid`; a session JWT carries no scopes.
	_, err = session.OpenIDConnect.OauthUserInfo(ctx)
	requireStatus(t, 401, err)
	// Only bodies the server refuses: a real change would alter how the other suites sign in.
	_, err = session.OrganizationAuthConfig.UpdateAuthMethod(ctx, operations.UpdateAuthMethodRequest{
		AuthMethod: []components.AuthStep{},
	})
	requireStatus(t, 400, err)
	_, err = session.OrganizationAuthConfig.UpdateAuthMethod(ctx, operations.UpdateAuthMethodRequest{
		AuthMethod: []components.AuthStep{{
			Order: 1,
			AllowedMethods: []components.AuthMethod{
				{Type: components.AuthMethodTypePassword},
				{Type: components.AuthMethodTypePassword},
			},
		}},
	})
	requireStatus(t, 400, err)
	_, err = session.UserAccount.ResetPassword(ctx, operations.ResetPasswordRequest{
		CurrentPassword: "sdk-test-wrong-current-password",
		NewPassword:     "Sdk-test-unused-password-1",
	})
	requireStatus(t, 401, err)

	_, err = session.UserAccount.InitAuth(ctx, &components.InitAuthRequest{Email: pipeshub.Pointer("sdk-test-not-an-email")})
	requireStatus(t, 400, err)
	_, err = session.UserAccount.Authenticate(ctx, "sdk-test-invalid-session", components.AuthenticateRequest{
		Method: components.MethodPassword,
		Credentials: components.CreateCredentialsPasswordCredentials(components.PasswordCredentials{
			Password: "sdk-test-wrong-password",
		}),
	})
	requireStatus(t, 401, err)
	_, err = session.UserAccount.RefreshToken(ctx, operations.RefreshTokenSecurity{ScopedToken: "sdk-test-invalid-refresh-token"})
	requireStatus(t, 401, err)

	// A personal access token works on the API but not on the routes that manage tokens and apps.
	created, err := session.PersonalAccessTokens.CreatePersonalAccessToken(ctx, components.CreatePatRequest{
		Name:       "sdk-test-pat-refused",
		Scopes:     []string{"openid", "kb:read"},
		ExpiryDays: pipeshub.Pointer(components.CreateExpiryDaysExpiryDaysEnum(components.ExpiryDaysEnumThirty)),
	})
	require.NoError(t, err)
	token := created.CreatePatResponse.Token
	pat := newBearerClient(token.AccessToken)
	_, err = pat.OAuthApps.ListOAuthApps(ctx, nil, nil, nil, nil)
	assert.Equal(t, 403, statusOf(err), "%v", err)
	_, err = pat.PersonalAccessTokens.CreatePersonalAccessToken(ctx, components.CreatePatRequest{Name: "sdk-test"})
	assert.Equal(t, 403, statusOf(err), "%v", err)
	_, err = session.PersonalAccessTokens.RevokePersonalAccessToken(ctx, token.ID, nil)
	require.NoError(t, err)
	_, err = pat.OpenIDConnect.OauthUserInfo(ctx)
	requireStatus(t, 401, err)
}
