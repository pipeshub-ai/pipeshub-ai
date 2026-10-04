package handwritten

import (
	"context"
	"io"
	"testing"

	pipeshub "github.com/pipeshub-ai/pipeshub-sdk-go"
	"github.com/pipeshub-ai/pipeshub-sdk-go/models/components"
	"github.com/pipeshub-ai/pipeshub-sdk-go/models/operations"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const (
	fileName    = "sdk-test-record.txt"
	fileContent = "PipesHub SDK test file.\n"
)

func TestHandwritten_RecordLifecycle(t *testing.T) {
	ctx := context.Background()
	s := newClient()

	kb, err := s.KnowledgeBase.CreateKnowledgeBase(ctx, operations.CreateKnowledgeBaseRequest{KbName: "sdk-test-records"})
	require.NoError(t, err)
	kbID := kb.KnowledgeBaseCreateResponse.ID
	defer func() {
		_, err := s.KnowledgeBase.DeleteKnowledgeBase(ctx, kbID)
		assert.NoError(t, err)
	}()

	upload, err := s.KnowledgeBase.UploadRecords(ctx, kbID, operations.UploadRecordsRequestBody{
		Files: []operations.UploadRecordsFile{{FileName: fileName, Content: []byte(fileContent)}},
	}, nil)
	require.NoError(t, err)
	var recordID string
	var summary any
	for _, e := range readAll(t, upload.UploadStreamSSEEvent) {
		switch e.Event {
		case "file:succeeded":
			recordID, _ = e.Data["recordId"].(string)
		case "done":
			summary = e.Data["summary"]
		}
	}
	require.NotEmpty(t, recordID, "upload did not succeed")
	assert.Equal(t, map[string]any{"total": 1.0, "succeeded": 1.0, "failed": 0.0}, summary)

	buffer, err := s.KnowledgeBase.StreamRecordBuffer(ctx, recordID, nil, nil)
	require.NoError(t, err)
	defer buffer.ResponseStream.Close()
	downloaded, err := io.ReadAll(buffer.ResponseStream)
	require.NoError(t, err)
	assert.Equal(t, fileContent, string(downloaded))

	updated, err := s.KnowledgeBase.UpdateRecord(ctx, recordID, &operations.UpdateRecordRequestBody{
		RecordName: pipeshub.Pointer("sdk-test-record-renamed"),
	})
	require.NoError(t, err)
	assert.Equal(t, pipeshub.Pointer(recordID), updated.Object.Record.ID)

	folder, err := s.KnowledgeBase.CreateFolder(ctx, kbID, operations.CreateFolderRequestBody{FolderName: "sdk-test-destination"}, nil)
	require.NoError(t, err)
	moved, err := s.KnowledgeBase.MoveRecord(ctx, kbID, recordID, components.KnowledgeBaseMoveRecordRequestBody{
		NewParentID: pipeshub.Pointer(folder.FolderCreateResponseSchema.ID),
	})
	require.NoError(t, err)
	assert.True(t, moved.KnowledgeBaseMoveRecordResponse.Success)

	reindexed, err := s.KnowledgeBase.ReindexRecord(ctx, recordID, nil)
	require.NoError(t, err)
	assert.True(t, reindexed.ReIndexRecordResponseSchema.Success)

	deleted, err := s.KnowledgeBase.DeleteRecord(ctx, recordID)
	require.NoError(t, err)
	assert.True(t, deleted.DeleteRecordResponseSchema.Success)
}
