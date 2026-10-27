package handwritten

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"

	pipeshub "github.com/pipeshub-ai/pipeshub-sdk-go"
	"github.com/pipeshub-ai/pipeshub-sdk-go/internal/utils"
	"github.com/pipeshub-ai/pipeshub-sdk-go/models/apierrors"
	"github.com/pipeshub-ai/pipeshub-sdk-go/models/components"
	"github.com/pipeshub-ai/pipeshub-sdk-go/types/stream"
	"github.com/stretchr/testify/require"
)

const botResponse = "bot_response"

// The SDK has one event type per streaming operation; they share this JSON shape.
// Data is the JSON text of the SSE frame's data, decoded by readAll.
type event struct {
	Event   string         `json:"event"`
	RawData string         `json:"data"`
	Data    map[string]any `json:"-"`
}

type message struct {
	ID          string `json:"_id"`
	MessageType string `json:"messageType"`
}

func serverURL() string {
	return utils.GetEnv("PIPESHUB_API_URL", "http://localhost:3000/api/v1")
}

func newClient() *pipeshub.Pipeshub {
	return pipeshub.New(
		pipeshub.WithServerURL(serverURL()),
		pipeshub.WithSecurity(components.Security{
			Oauth2: &components.SchemeOauth2{
				ClientID:     utils.GetEnv("PIPESHUB_CLIENT_ID", ""),
				ClientSecret: utils.GetEnv("PIPESHUB_CLIENT_SECRET", ""),
				TokenURL:     utils.GetEnv("PIPESHUB_TOKEN_URL", "http://localhost:3000/api/v1/oauth2/token"),
			},
		}),
	)
}

// newBearerClient sends token as the bearer: a session JWT or a personal access token.
func newBearerClient(token string) *pipeshub.Pipeshub {
	return pipeshub.New(
		pipeshub.WithServerURL(serverURL()),
		pipeshub.WithSecurity(components.Security{BearerAuth: pipeshub.Pointer(token)}),
	)
}

// newAdminClient is for team connectors and OAuth configurations, which are admin-only: the OAuth
// app's tokens act as a service account, which is never an admin.
func newAdminClient() *pipeshub.Pipeshub {
	return newSessionClient()
}

// newSessionClient is for the OAuth app and personal access token routes, which accept only an
// interactive session.
func newSessionClient() *pipeshub.Pipeshub {
	return newBearerClient(utils.GetEnv("PIPESHUB_SESSION_TOKEN", ""))
}

// uniqueName is for names that must be unique in an org, such as a connector's.
func uniqueName(t *testing.T, prefix string) string {
	t.Helper()
	suffix := make([]byte, 8)
	_, err := rand.Read(suffix)
	require.NoError(t, err)
	return prefix + "-" + hex.EncodeToString(suffix)
}

// header reads a response header from the SDK's header map, whatever the case of its keys.
func header(headers map[string][]string, name string) []string {
	for key, values := range headers {
		if strings.EqualFold(key, name) {
			return values
		}
	}
	return nil
}

// requireStatus checks that a call failed with the given HTTP status.
func requireStatus(t *testing.T, status int, err error) {
	t.Helper()
	require.Error(t, err, "expected HTTP %d, the call succeeded", status)
	require.Equal(t, status, statusOf(err), "%v", err)
}

func statusOf(err error) int {
	var apiErr *apierrors.APIError
	if errors.As(err, &apiErr) {
		return apiErr.StatusCode
	}
	var validationErr *apierrors.ResponseValidationError
	if errors.As(err, &validationErr) {
		return validationErr.StatusCode
	}
	// The typed errors of the spec's error responses carry the response in HTTPMeta.
	value := reflect.Indirect(reflect.ValueOf(err))
	if value.Kind() == reflect.Struct {
		if field := value.FieldByName("HTTPMeta"); field.IsValid() {
			if meta, ok := field.Interface().(components.HTTPMetadata); ok && meta.Response != nil {
				return meta.Response.StatusCode
			}
		}
	}
	return 0
}

// recode converts an SDK model to a local type through its JSON form.
func recode(t *testing.T, from any, to any) {
	t.Helper()
	raw, err := json.Marshal(from)
	require.NoError(t, err)
	require.NoError(t, json.Unmarshal(raw, to))
}

func readAll[T any](t *testing.T, events *stream.EventStream[T]) []event {
	t.Helper()
	require.NotNil(t, events)
	defer events.Close()

	var all []event
	for events.Next() {
		var e event
		recode(t, events.Value(), &e)
		if e.RawData != "" {
			require.NoError(t, json.Unmarshal([]byte(e.RawData), &e.Data))
		}
		all = append(all, e)
	}
	require.NoError(t, events.Err())
	return all
}

func names(events []event) []string {
	all := make([]string, len(events))
	for i, e := range events {
		all[i] = e.Event
	}
	return all
}

// requireFinishedRun checks that a chat stream that was read to its end produced an answer.
func requireFinishedRun(t *testing.T, events []event) {
	t.Helper()
	for _, e := range events {
		require.NotEqual(t, "RUN_ERROR", e.Event, "stream reported an error: %v", e.Data)
	}
	require.Contains(t, names(events), "TEXT_MESSAGE_CONTENT", "no answer text in the stream")
	require.Contains(t, names(events), "RUN_FINISHED", "stream ended without RUN_FINISHED")
}

func createdConversationID(t *testing.T, events []event) string {
	t.Helper()
	for _, e := range events {
		if e.Event != "CUSTOM" || e.Data["name"] != "conversation_created" {
			continue
		}
		value, _ := e.Data["value"].(map[string]any)
		id, _ := value["conversationId"].(string)
		require.NotEmpty(t, id)
		return id
	}
	require.FailNow(t, "the stream did not announce a new conversation")
	return ""
}

func lastAnswerID(t *testing.T, sdkMessages any) string {
	t.Helper()
	var messages []message
	recode(t, sdkMessages, &messages)
	for i := len(messages) - 1; i >= 0; i-- {
		if messages[i].MessageType == botResponse {
			require.NotEmpty(t, messages[i].ID)
			return messages[i].ID
		}
	}
	require.FailNow(t, "the conversation has no answer message")
	return ""
}
