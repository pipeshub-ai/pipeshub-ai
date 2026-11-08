package handwritten

import (
	"context"
	"testing"

	pipeshub "github.com/pipeshub-ai/pipeshub-sdk-go/v2"
	"github.com/pipeshub-ai/pipeshub-sdk-go/v2/models/components"
	"github.com/pipeshub-ai/pipeshub-sdk-go/v2/models/operations"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestHandwritten_ConversationLifecycle(t *testing.T) {
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

	titled, err := s.Conversations.UpdateConversationTitle(ctx, conversationID, operations.UpdateConversationTitleRequestBody{
		Title: "sdk-test-title",
	})
	require.NoError(t, err)
	assert.Equal(t, pipeshub.Pointer("sdk-test-title"), titled.Object.Conversation.Title)

	archived, err := s.Conversations.ArchiveConversation(ctx, conversationID)
	require.NoError(t, err)
	assert.Equal(t, pipeshub.Pointer(operations.ArchiveConversationStatusArchived), archived.Object.Status)

	unarchived, err := s.Conversations.UnarchiveConversation(ctx, conversationID)
	require.NoError(t, err)
	assert.Equal(t, pipeshub.Pointer(operations.UnarchiveConversationStatusUnarchived), unarchived.Object.Status)
}
