'use client';

export {
  useAuthStore,
  type User,
  logoutAndRedirect,
  logoutFromWorkspaceMenu,
  switchToSignedInWorkspace,
  ELECTRON_SERVER_URL_NAVIGATION_EVENT,
} from '@/lib/store/auth-store';
export { useInitializeUserProfile } from '@/lib/hooks/use-initialize-user-profile';
export { useInitializeOrgProfile } from '@/lib/hooks/use-initialize-org-profile';

export {
  useOrgStore,
  selectIsOrgInitialized,
  type OrgStore,
} from '@/lib/store/org-store';

// API (axios instance — consumed via the `@/lib/api` barrel)
export { apiClient, default as apiClientDefault } from '@/lib/api/axios-instance';

// UI Components
export { SettingsSection } from '@/app/components/workspace-menu/settings-section';
export { WorkspaceMenu } from '@/app/components/workspace-menu/menu';
export { GuestGuard } from '@/app/components/ui/guest-guard';
export { AgentBuilder } from '@/app/(main)/agents/agent-builder/agent-builder';
export { CreateAgentDialog } from '@/app/(main)/agents/components/create-agent-dialog';
export { AgentSidebarListRow } from '@/app/(main)/chat/sidebar/agent-sidebar-list-row';
export { ChatSidebarFooter } from '@/app/(main)/chat/sidebar/footer';
export { AgentChatHeader } from '@/app/(main)/chat/components/agent-chat-header';
export { OAuthAppSelector } from '@/app/(main)/workspace/connectors/components/authenticate-tab/oauth-app-selector';
// Users page header — Requests + Blocked Users (Admin Org) between search and Invite
export { UsersPageHeaderActions } from '@/app/(main)/workspace/users/components/users-page-header-actions';
export { isMcpInstanceReadOnly, McpInheritedBadge, McpInheritedCallout } from '@/app/(main)/workspace/mcp-servers/team/components/mcp-inheritance-guards';
export { useUserPermission } from '@/lib/hooks/use-user-permission';
export { PermissionLockIcon } from '@/app/components/ui/permission-lock-icon';
export { PermissionDeniedDialog, usePermissionDeniedDialog } from '@/app/components/ui/permission-denied-dialog';
export { InheritedConfigNotice } from '@/app/(main)/workspace/components/inherited-config-notice';
export { KbListView } from '@/app/(main)/knowledge-base/components/kb-list-view';
export { KbGridView } from '@/app/(main)/knowledge-base/components/kb-grid-view';
export { FolderTreeItem } from '@/app/(main)/knowledge-base/sidebar/section-element/folder-tree-item';
export type { FolderTreeItemProps } from '@/app/(main)/knowledge-base/sidebar/section-element/folder-tree-item';
export { SelectionActionBar } from '@/app/(main)/knowledge-base/components/selection-action-bar';
export { useShouldPollServiceHealth } from '@/lib/hooks/use-should-poll-service-health';
// Brand context; pass loadBrand to read the brand from somewhere (OSS: PipesHub defaults)
export { BrandProvider } from '@/lib/branding';
export { BrandMark } from '@/app/components/ui/brand-mark';
export { BrandWordmark } from '@/app/components/ui/brand-wordmark';
// App-shell extras and the invite-users sidebar (OSS: nothing / the OSS sidebar)
export { AppLayoutExtras } from '@/app/(main)/components/app-layout-extras';
export { InviteUsersSidebar } from '@/app/(main)/workspace/users/components/invite-users-sidebar';
// Extra details editions can show in chat, search, knowledge-base and connector views (OSS: nothing)
export { ChatMessageExtras } from '@/app/(main)/chat/components/message-area/message-extras';
export { ConversationHeaderOverlay } from '@/app/(main)/chat/components/conversation-header-overlay';
export { SearchResultsExtras } from '@/app/(main)/chat/components/search-results/search-results-extras';
export { useKbListExtraColumns } from '@/app/(main)/knowledge-base/hooks/use-kb-list-extra-columns';
export { ConnectorOverviewExtras } from '@/app/(main)/workspace/connectors/components/instance-panel/connector-overview-extras';
// Team connectors to warn about before setup: their data is searchable by the whole organization (OSS: no source permissions applied)
export { TEAM_CONNECTOR_VISIBILITY_WARNINGS } from '@/app/(main)/workspace/connectors/utils/team-visibility-warning';
// A toolset's "Tool approvals" (OSS: what its tools do by default; organization rules aren't set here)
export { ToolsetToolRulesNotice as ToolsetToolRulesDialog } from '@/app/(main)/workspace/actions/components/toolset-tool-rules-notice';
// Notices for features this edition does not include (OSS: shown)
export { EnterpriseFeatureNotice } from '@/app/components/ui/enterprise-badge';
export { McpAgentApprovalsNotice } from '@/app/(main)/agents/agent-builder/components/mcp-agent-approvals-notice';


