package handwritten

import (
	"context"
	"net/url"
	"testing"

	pipeshub "github.com/pipeshub-ai/pipeshub-sdk-go"
	"github.com/pipeshub-ai/pipeshub-sdk-go/models/components"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// Completing the flow needs a person to consent in a browser, so this stops at the consent URL.
func TestHandwritten_ConnectorOAuthAuthorization(t *testing.T) {
	ctx := context.Background()
	s := newAdminClient()
	// OAuth app and connector names are unique in an org.
	name := uniqueName(t, "sdk-test-jira")

	config, err := s.OAuthConfiguration.CreateOAuthConfig(ctx, "Jira", components.CreateOAuthConfigRequest{
		OauthInstanceName: name,
		Config: components.CreateOAuthConfigRequestConfig{
			ClientID:     pipeshub.Pointer("sdk-test-client-id"),
			ClientSecret: pipeshub.Pointer("sdk-test-client-secret"),
		},
	})
	require.NoError(t, err)
	configID := *config.Object.OauthConfig.ID
	defer func() {
		_, err := s.OAuthConfiguration.DeleteOAuthConfig(ctx, "Jira", configID)
		assert.NoError(t, err)
	}()

	created, err := s.ConnectorInstances.CreateConnectorInstance(ctx, components.CreateConnectorRequest{
		ConnectorType: "Jira",
		InstanceName:  name,
		Scope:         components.ConnectorScopeTeam,
		AuthType:      pipeshub.Pointer(components.ConnectorAuthTypeOauth),
		Config: &components.CreateConnectorRequestConfig{
			Auth: &components.ConnectorAuthConfig{OauthConfigID: pipeshub.Pointer(configID)},
		},
	})
	require.NoError(t, err)
	connectorID := created.Object.Connector.ConnectorID
	defer func() {
		_, err := s.ConnectorInstances.DeleteConnectorInstance(ctx, connectorID)
		assert.NoError(t, err)
	}()

	authorization, err := s.ConnectorOAuth.GetOAuthAuthorizationURL(ctx, connectorID, nil)
	require.NoError(t, err)
	consent, err := url.Parse(authorization.Object.AuthorizationURL)
	require.NoError(t, err)
	assert.Equal(t, "https://auth.atlassian.com", consent.Scheme+"://"+consent.Host)
	assert.Equal(t, "sdk-test-client-id", consent.Query().Get("client_id"))
	assert.NotEmpty(t, authorization.Object.State)
}
