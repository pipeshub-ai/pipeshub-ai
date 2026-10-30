package handwritten

import (
	"context"
	"testing"

	pipeshub "github.com/pipeshub-ai/pipeshub-sdk-go/v2"
	"github.com/pipeshub-ai/pipeshub-sdk-go/v2/internal/utils"
	"github.com/pipeshub-ai/pipeshub-sdk-go/v2/models/components"
	"github.com/pipeshub-ai/pipeshub-sdk-go/v2/models/operations"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestHandwritten_SignIn(t *testing.T) {
	ctx := context.Background()
	s := newClient()
	email := utils.GetEnv("PIPESHUB_TEST_USER_EMAIL", "")

	init, err := s.UserAccount.InitAuth(ctx, &components.InitAuthRequest{Email: pipeshub.Pointer(email)})
	require.NoError(t, err)
	assert.Contains(t, init.InitAuthResponse.AllowedMethods, components.AllowedMethodPassword)
	sessionTokens := header(init.Headers, "x-session-token")
	require.Len(t, sessionTokens, 1, "initAuth returned no x-session-token")
	sessionToken := sessionTokens[0]

	signedIn, err := s.UserAccount.Authenticate(ctx, sessionToken, components.AuthenticateRequest{
		Method: components.MethodPassword,
		Email:  pipeshub.Pointer(email),
		Credentials: components.CreateCredentialsPasswordCredentials(components.PasswordCredentials{
			Password: utils.GetEnv("PIPESHUB_TEST_USER_PASSWORD", ""),
		}),
	})
	require.NoError(t, err)
	final := signedIn.AuthenticateResponse.AuthenticateFinalResponse
	require.NotNil(t, final, "sign-in did not finish: %+v", signedIn.AuthenticateResponse)
	assert.NotEmpty(t, final.AccessToken)

	refreshed, err := s.UserAccount.RefreshToken(ctx, operations.RefreshTokenSecurity{ScopedToken: final.RefreshToken})
	require.NoError(t, err)
	assert.NotEmpty(t, refreshed.RefreshTokenResponse.AccessToken)
	assert.Equal(t, email, refreshed.RefreshTokenResponse.User.Email)
}
