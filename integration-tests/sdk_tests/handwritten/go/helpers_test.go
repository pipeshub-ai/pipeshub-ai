package handwritten

import (
	"encoding/json"
	"testing"

	pipeshub "github.com/pipeshub-ai/pipeshub-sdk-go"
	"github.com/pipeshub-ai/pipeshub-sdk-go/internal/utils"
	"github.com/pipeshub-ai/pipeshub-sdk-go/models/components"
	"github.com/pipeshub-ai/pipeshub-sdk-go/types/stream"
	"github.com/stretchr/testify/require"
)

const botResponse = "bot_response"

// The SDK has one event type per streaming operation; they share this JSON shape.
type event struct {
	Event string         `json:"event"`
	Data  map[string]any `json:"data"`
}

type message struct {
	ID          string `json:"_id"`
	MessageType string `json:"messageType"`
}

func newClient() *pipeshub.Pipeshub {
	return pipeshub.New(
		pipeshub.WithServerURL(utils.GetEnv("PIPESHUB_API_URL", "http://localhost:3000/api/v1")),
		pipeshub.WithSecurity(components.Security{
			Oauth2: &components.SchemeOauth2{
				ClientID:     utils.GetEnv("PIPESHUB_CLIENT_ID", ""),
				ClientSecret: utils.GetEnv("PIPESHUB_CLIENT_SECRET", ""),
				TokenURL:     utils.GetEnv("PIPESHUB_TOKEN_URL", "http://localhost:3000/api/v1/oauth2/token"),
			},
		}),
	)
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
