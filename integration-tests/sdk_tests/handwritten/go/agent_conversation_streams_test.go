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

func TestHandwritten_AgentConversationStreams(t *testing.T) {
	ctx := context.Background()
	s := newClient()

	created, err := s.Agents.CreateAgent(ctx, components.AgentCreateRequest{Name: "sdk-test-agent-streams"})
	require.NoError(t, err)
	agentKey := created.AgentCreateResponse.Agent.Key
	// Deleting the agent removes its conversations with it.
	defer func() {
		_, err := s.Agents.DeleteAgent(ctx, agentKey)
		assert.NoError(t, err)
	}()

	chat, err := s.Agents.StreamAgentConversation(ctx, agentKey, components.AgentStreamCreateConversationRequest{
		Query:    "Reply with the single word OK.",
		ChatMode: components.AgentStreamCreateConversationRequestChatModeQuick,
	})
	require.NoError(t, err)
	events := readAll(t, chat.AgentStreamSSEEvent)
	requireFinishedRun(t, events)
	conversationID := createdConversationID(t, events)

	followUp, err := s.Agents.StreamAgentConversationMessage(ctx, agentKey, conversationID, components.AgentAddMessageStreamRequest{
		Query:    "Reply with the single word YES.",
		ChatMode: components.AgentAddMessageStreamRequestChatModeQuick,
	})
	require.NoError(t, err)
	requireFinishedRun(t, readAll(t, followUp.AgentMessageStreamSSEEvent))

	detail, err := s.Agents.GetAgentConversationByID(ctx, operations.GetAgentConversationByIDRequest{
		AgentKey:       agentKey,
		ConversationID: conversationID,
	})
	require.NoError(t, err)
	answerID := lastAnswerID(t, detail.AgentConversationDetailResponse.Conversation.Messages)

	regenerated, err := s.Agents.RegenerateAgentConversationMessage(ctx, agentKey, conversationID, answerID, components.AgentRegenerateRequest{
		ChatMode: components.AgentRegenerateRequestChatModeQuick,
	})
	require.NoError(t, err)
	requireFinishedRun(t, readAll(t, regenerated.AgentRegenerateSSEEvent))

	feedback, err := s.Agents.UpdateAgentConversationMessageFeedback(ctx, agentKey, conversationID, answerID, components.MessageFeedbackSubmitRequest{
		IsHelpful: pipeshub.Pointer(true),
	})
	require.NoError(t, err)
	assert.Equal(t, answerID, feedback.MessageFeedbackUpdateResponse.MessageID)
	assert.Equal(t, pipeshub.Pointer(true), feedback.MessageFeedbackUpdateResponse.Feedback.IsHelpful)

	fork, err := s.Agents.ForkAgentConversation(ctx, agentKey, conversationID, answerID)
	require.NoError(t, err)
	assert.Equal(t, components.ForkedFrom{ConversationID: conversationID, MessageID: answerID},
		fork.ForkConversationResponse.ForkedFrom)

	upload, err := s.Agents.UploadAgentConversationChatAttachments(ctx, agentKey, operations.UploadAgentConversationChatAttachmentsRequestBody{
		ConversationID: pipeshub.Pointer(conversationID),
		Files: []operations.UploadAgentConversationChatAttachmentsFile{
			{FileName: "sdk-test-attachment.txt", Content: []byte("PipesHub SDK test attachment.\n")},
		},
	})
	require.NoError(t, err)
	assert.Equal(t, pipeshub.Pointer(conversationID), upload.ChatAttachmentUploadResponse.ConversationID)
	require.NotEmpty(t, upload.ChatAttachmentUploadResponse.Attachments, "upload returned no attachment")
	recordID := upload.ChatAttachmentUploadResponse.Attachments[0].RecordID
	_, err = s.Agents.DeleteAgentConversationChatAttachment(ctx, agentKey, recordID)
	require.NoError(t, err)
	_, err = s.Agents.DeleteAgentConversationChatAttachment(ctx, agentKey, recordID)
	requireStatus(t, 404, err)
}
