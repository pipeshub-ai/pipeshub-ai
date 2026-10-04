package handwritten

import (
	"context"
	"testing"

	pipeshub "github.com/pipeshub-ai/pipeshub-sdk-go"
	"github.com/pipeshub-ai/pipeshub-sdk-go/models/components"
	"github.com/pipeshub-ai/pipeshub-sdk-go/models/operations"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestHandwritten_ConversationStreams(t *testing.T) {
	ctx := context.Background()
	s := newClient()

	chat, err := s.Conversations.StreamChat(ctx, components.ConversationStreamRequest{
		Query:    "Reply with the single word OK.",
		ChatMode: components.ConversationStreamRequestChatModeInternalSearch,
	})
	require.NoError(t, err)
	events := readAll(t, chat.ConversationStreamSSEEvent)
	conversationID := createdConversationID(t, events)
	defer func() {
		_, err := s.Conversations.DeleteConversationByID(ctx, conversationID)
		assert.NoError(t, err)
	}()
	requireFinishedRun(t, events)

	followUp, err := s.Conversations.AddMessageStream(ctx, conversationID, components.ConversationMessageStreamRequest{
		Query:    "Reply with the single word YES.",
		ChatMode: components.ConversationMessageStreamRequestChatModeInternalSearch,
	})
	require.NoError(t, err)
	requireFinishedRun(t, readAll(t, followUp.ConversationMessageStreamSSEEvent))

	detail, err := s.Conversations.GetConversationByID(ctx, operations.GetConversationByIDRequest{
		ConversationID: conversationID,
	})
	require.NoError(t, err)
	answerID := lastAnswerID(t, detail.Object.Conversation.Messages)

	regenerated, err := s.Conversations.RegenerateAnswer(ctx, conversationID, answerID, &components.RegenerateRequest{
		ChatMode: pipeshub.Pointer("internal_search"),
	})
	require.NoError(t, err)
	requireFinishedRun(t, readAll(t, regenerated.SSEEvent))

	feedback, err := s.Conversations.UpdateMessageFeedback(ctx, conversationID, answerID, components.MessageFeedbackSubmitRequest{
		IsHelpful: pipeshub.Pointer(true),
	})
	require.NoError(t, err)
	assert.Equal(t, answerID, feedback.MessageFeedbackUpdateResponse.MessageID)
	assert.Equal(t, pipeshub.Pointer(true), feedback.MessageFeedbackUpdateResponse.Feedback.IsHelpful)
}
