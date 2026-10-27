package handwritten

import (
	"context"
	"os"
	"testing"
	"time"

	pipeshub "github.com/pipeshub-ai/pipeshub-sdk-go"
	"github.com/pipeshub-ai/pipeshub-sdk-go/models/components"
	"github.com/pipeshub-ai/pipeshub-sdk-go/models/operations"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const syncTimeout = 5 * time.Minute

// feedURL is the feed the sync test reads: by default the integration stack's web-fixtures
// feed, so the sync does not depend on the internet.
func feedURL() string {
	if url := os.Getenv("PIPESHUB_SDK_TEST_RSS_FEED_URL"); url != "" {
		return url
	}
	return "http://web-fixtures:8080/feeds/news.xml"
}

func waitForIdle(t *testing.T, s *pipeshub.Pipeshub, connectorID string) {
	t.Helper()
	deadline := time.Now().Add(syncTimeout)
	for {
		got, err := s.ConnectorInstances.GetConnectorInstance(context.Background(), connectorID)
		require.NoError(t, err)
		var connector struct {
			IsActive bool    `json:"isActive"`
			IsLocked bool    `json:"isLocked"`
			Status   *string `json:"status"`
		}
		recode(t, got.Object.Connector, &connector)
		if connector.IsActive && !connector.IsLocked && connector.Status != nil && *connector.Status == "IDLE" {
			return
		}
		require.True(t, time.Now().Before(deadline), "the sync did not finish: %+v", connector)
		time.Sleep(2 * time.Second)
	}
}

func TestHandwritten_ConnectorSync(t *testing.T) {
	ctx := context.Background()
	s := newAdminClient()

	created, err := s.ConnectorInstances.CreateConnectorInstance(ctx, components.CreateConnectorRequest{
		ConnectorType: "RSS",
		// Connector names are unique in an org.
		InstanceName: uniqueName(t, "sdk-test-rss"),
		Scope:        components.ConnectorScopeTeam,
		AuthType:     pipeshub.Pointer(components.ConnectorAuthTypeNone),
		Config: &components.CreateConnectorRequestConfig{
			Sync: &components.ConnectorSyncConfig{AdditionalProperties: map[string]any{
				"feed_urls":             feedURL(),
				"max_articles_per_feed": 1,
				"fetch_full_content":    false,
			}},
		},
	})
	require.NoError(t, err)
	connectorID := created.Object.Connector.ConnectorID
	deleted := false
	defer func() {
		if !deleted {
			_, err := s.ConnectorInstances.DeleteConnectorInstance(ctx, connectorID)
			assert.NoError(t, err)
		}
	}()

	// RSS has no auth, so its saved settings are enough for filter options, but none of them
	// come from the service. It cannot resync before it is enabled.
	filters, err := s.ConnectorFilters.GetConnectorFilters(ctx, connectorID)
	require.NoError(t, err)
	assert.True(t, filters.Object.Success)
	assert.Empty(t, filters.Object.FilterOptions)
	_, err = s.ConnectorFilters.GetFilterFieldOptions(ctx, operations.GetFilterFieldOptionsRequest{
		ConnectorID: connectorID,
		FilterKey:   "sdk_test_no_such_filter",
	})
	requireStatus(t, 404, err)
	_, err = s.ConnectorInstances.ResyncConnector(ctx, connectorID, components.ResyncConnectorRequestBody{ConnectorName: "RSS"})
	requireStatus(t, 400, err)

	toggled, err := s.ConnectorControl.ToggleConnector(ctx, connectorID, components.ConnectorToggleRequest{
		Type: components.ConnectorToggleRequestTypeSync,
	})
	require.NoError(t, err)
	assert.True(t, toggled.Object.Success)
	waitForIdle(t, s, connectorID)

	stats, err := s.ConnectorInstances.GetConnectorStats(ctx, connectorID)
	require.NoError(t, err)
	assert.Equal(t, connectorID, stats.ConnectorStats.Data.ConnectorID)

	app, err := s.KnowledgeHub.GetKnowledgeHubChildNodes(ctx, operations.GetKnowledgeHubChildNodesRequest{
		ParentType: operations.ParentTypeApp,
		ParentID:   connectorID,
	})
	require.NoError(t, err)
	var feed *components.KnowledgeHubNode
	for i, item := range app.KnowledgeHubNodesResponse.Items {
		if item.NodeType == components.NodeTypeRecordGroup {
			feed = &app.KnowledgeHubNodesResponse.Items[i]
			break
		}
	}
	require.NotNil(t, feed, "the sync made no record group")
	reindexed, err := s.KnowledgeBase.ReindexRecordGroup(ctx, feed.ID, &components.ReindexRecordGroupRequestBody{
		Depth: pipeshub.Pointer[int64](0),
	})
	require.NoError(t, err)
	assert.Equal(t, feed.ID, reindexed.ReIndexRecordGroupResponseSchema.RecordGroupID)

	group, err := s.KnowledgeHub.GetKnowledgeHubChildNodes(ctx, operations.GetKnowledgeHubChildNodesRequest{
		ParentType: operations.ParentTypeRecordGroup,
		ParentID:   feed.ID,
	})
	require.NoError(t, err)
	var article *components.KnowledgeHubNode
	for i, item := range group.KnowledgeHubNodesResponse.Items {
		if item.NodeType == components.NodeTypeRecord && item.WebURL != nil {
			article = &group.KnowledgeHubNodesResponse.Items[i]
			break
		}
	}
	require.NotNil(t, article, "the sync made no record with a URL")

	lookup, err := s.Connector.LookupRecordByIdentifier(ctx, []string{*article.WebURL}, nil)
	require.NoError(t, err)
	var matched []string
	for _, match := range lookup.LookupRecordResponseSchema.Matches {
		matched = append(matched, match.ID)
	}
	assert.Contains(t, matched, article.ID)

	content, err := s.Connector.GetRecordContent(ctx, operations.GetRecordContentRequest{RecordID: article.ID})
	require.NoError(t, err)
	assert.Contains(t, content.GetRecordContentResponseSchema.Content, article.ID)

	resynced, err := s.ConnectorInstances.ResyncConnector(ctx, connectorID, components.ResyncConnectorRequestBody{ConnectorName: "RSS"})
	require.NoError(t, err)
	assert.True(t, resynced.Object.ResyncConnectorResponse.Success)

	stopped, err := s.ConnectorInstances.StopConnectorSync(ctx, connectorID)
	require.NoError(t, err)
	assert.True(t, stopped.Object.Success)

	_, err = s.ConnectorInstances.DeleteConnectorInstance(ctx, connectorID)
	require.NoError(t, err)
	deleted = true
}
