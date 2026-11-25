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
// Sign-in answers and notices a build adds (OSS: none; a refused sign-in shows its ordinary error)
export { takeSignInRefusal } from '@/lib/auth/sign-in-refusal';
export { SignInNotices } from '@/app/(public)/components/sign-in-notices';
export { AgentBuilder } from '@/app/(main)/agents/agent-builder/agent-builder';
export { CreateAgentDialog } from '@/app/(main)/agents/components/create-agent-dialog';
export { AgentSidebarListRow } from '@/app/(main)/chat/sidebar/agent-sidebar-list-row';
export { ChatSidebarFooter } from '@/app/(main)/chat/sidebar/footer';
export { AgentChatHeader } from '@/app/(main)/chat/components/agent-chat-header';
export { OAuthAppSelector } from '@/app/(main)/workspace/connectors/components/authenticate-tab/oauth-app-selector';
// Users page header — Requests + Blocked Users (Admin Org) between search and Invite
export { UsersPageHeaderActions } from '@/app/(main)/workspace/users/components/users-page-header-actions';
// Row actions an edition adds to a member on the Users page (OSS: none)
export { useMemberAccountActions } from '@/app/(main)/workspace/users/components/member-account-actions';
export { isMcpInstanceReadOnly, McpInheritedBadge, McpInheritedCallout, McpInstanceBadges } from '@/app/(main)/workspace/mcp-servers/team/components/mcp-inheritance-guards';
// Who may use an organization MCP server (OSS: everyone, so nothing to manage)
export { McpManageAccess } from '@/app/(main)/workspace/mcp-servers/team/components/mcp-manage-access';
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
// Extra content on a person's profile panel (OSS: nothing)
export { PersonPanelExtras } from '@/app/(main)/workspace/users/components/person-panel-extras';
// Extra details editions can show in chat, search, knowledge-base and connector views (OSS: a short notice at most)
export { ChatMessageExtras } from '@/app/(main)/chat/components/message-area/message-extras';
// More of the thumbs-down popover, adding fields to the rating (OSS: nothing)
export { FeedbackDislikeExtras } from '@/app/(main)/chat/components/message-area/feedback-dislike-extras';
export type { FeedbackDislikeExtrasProps } from '@/app/(main)/chat/components/message-area/feedback-dislike-extras';
export { ConversationHeaderOverlay } from '@/app/(main)/chat/components/conversation-header-overlay';
export { SearchResultsExtras } from '@/app/(main)/chat/components/search-results/search-results-extras';
// Extra actions in the knowledge-base selection bar, and extras for uploading into a collection (OSS: none)
export { KbSelectionActions } from '@/app/(main)/knowledge-base/components/kb-selection-actions';
export { useKbUploadExtras } from '@/app/(main)/knowledge-base/hooks/use-kb-upload-extras';
export { useKbListExtraColumns } from '@/app/(main)/knowledge-base/hooks/use-kb-list-extra-columns';
export { ConnectorOverviewExtras } from '@/app/(main)/workspace/connectors/components/instance-panel/connector-overview-extras';
export { ConnectorConfigureExtras } from '@/app/(main)/workspace/connectors/components/configure-tab/connector-configure-extras';
// Team connectors to warn about before setup: their data is searchable by the whole organization (OSS: no source permissions applied)
export { TEAM_CONNECTOR_VISIBILITY_WARNINGS } from '@/app/(main)/workspace/connectors/utils/team-visibility-warning';
// A toolset's "Tool approvals" (OSS: what its tools do by default; organization rules aren't set here)
export { ToolsetToolRulesNotice as ToolsetToolRulesDialog } from '@/app/(main)/workspace/actions/components/toolset-tool-rules-notice';
// Notices for features this edition does not include (OSS: shown)
export { EnterpriseFeatureNotice } from '@/app/components/ui/enterprise-badge';
export { McpAgentApprovalsNotice } from '@/app/(main)/agents/agent-builder/components/mcp-agent-approvals-notice';
// Rules beyond the usual one that decide who approves an ask, and the card's note for one (OSS: none; nothing shown)
export { approvalAskRules } from '@/app/(main)/chat/approval-ask-rules';
export type { ApprovalAskRules } from '@/app/(main)/chat/approval-ask-rules';
export { ApprovalAskNote } from '@/app/(main)/chat/components/message-area/approval-ask-note';
export type { ApprovalAskNoteProps } from '@/app/(main)/chat/components/message-area/approval-ask-note';
// What an answer stopped by a budget beyond the run's own says (OSS: nothing; the spending-limit notice shows)
export { budgetStopLines } from '@/app/(main)/chat/budget-stop-lines';
// Run endings an edition's own rules end runs with, worded, with their next step (OSS: none)
export { runErrorExtras } from '@/app/(main)/chat/run-error-extras';
export type { RunErrorExtras } from '@/app/(main)/chat/run-error-extras';
// A note and more sections on a person's "My AI accounts" page (OSS: none)
export { MyAiAccountsNotice, MyAiAccountsSections } from '@/app/(main)/workspace/ai-accounts/components/my-ai-accounts-extras';
// More settings cards at the end of the AI Access page (OSS: none)
export { AiAccessExtras } from '@/app/(main)/workspace/ai-access/components/ai-access-extras';
// Rows the Services page adds after its own (OSS: none)
export { serviceRowExtras } from '@/app/(main)/workspace/services/service-row-extras';
export type { ServiceRowExtra } from '@/app/(main)/workspace/services/service-row-extras';
// More settings cards on the general page (OSS: none)
export { GeneralSettingsExtras } from '@/app/(main)/workspace/general/components/general-settings-extras';
// The general page's delete-the-organization section (OSS: deleted at once behind the typed name)
export { DeleteOrgSection } from '@/app/(main)/workspace/general/components/delete-org-section';
// Extra settings in the agent card's "How it runs" group (OSS: none)
export { AgentRuntimeExtras } from '@/app/(main)/agents/agent-builder/components/agent-runtime-extras';
// Extra cards at the end of the agent card's "How it runs" group, after its schedules (OSS: none)
export { AgentRuntimeTailExtras } from '@/app/(main)/agents/agent-builder/components/agent-runtime-tail-extras';
// More of the agent's environment in the builder's "Environment" section (OSS: none)
export { AgentEnvironmentExtras } from '@/app/(main)/agents/agent-builder/components/agent-environment-extras';
export type { AgentEnvironmentExtrasProps } from '@/app/(main)/agents/agent-builder/components/agent-environment-extras';
// Settings cards after the org's default coding engine on AI Access (OSS: none)
export { AiAccessControls } from '@/app/(main)/workspace/ai-access/components/ai-access-controls';
// More engine settings on AI Access (OSS: none)
export { AiAccessEnginesExtras } from '@/app/(main)/workspace/ai-access/components/ai-access-engines-extras';
// Chat model menu entries and AI model form settings (OSS: none)
export { chatModelOptions } from '@/app/(main)/chat/utils/chat-model-options';
export { modelUnavailableReasons } from '@/app/(main)/chat/utils/model-unavailable-reasons';
export type { ModelUnavailableReasons } from '@/app/(main)/chat/utils/model-unavailable-reasons';
export { ModelSelectorExtraOptions } from '@/app/(main)/chat/components/chat-panel/expansion-panels/model-selector/model-selector-extra-options';
export { aiModelFormOptions } from '@/app/(main)/workspace/ai-models/ai-model-form-options';
export { PickedModelExtraFields } from '@/app/(main)/workspace/ai-models/components/picked-model-extra-fields';
// What the chat's "Clone not available" card offers an org admin (OSS: the card's own line)
export { CloneUnavailableAdminAction } from '@/app/(main)/chat/components/message-area/clone-unavailable-admin-action';
export type { CloneUnavailableAdminActionProps } from '@/app/(main)/chat/components/message-area/clone-unavailable-admin-action';
// A tab beside a person's own schedules, for an admin (OSS: none)
export { SchedulesOrgTab } from '@/app/(main)/workspace/schedules/components/schedules-org-tab';
// Entries an edition adds to the workspace sidebar (OSS: none)
export { workspaceNavExtras } from '@/app/(main)/workspace/sidebar/workspace-nav-extras';
export type { WorkspaceNavExtra, WorkspaceNavSection } from '@/app/(main)/workspace/sidebar/workspace-nav';
// Pages an edition adds that shared views link to, by name (OSS: none; the views show no link)
export { workspacePageLinks } from '@/app/(main)/workspace/sidebar/workspace-page-links';
// An org MCP server's panel: a tab and settings an edition adds (OSS: none)
export { McpInstanceConfigExtras, useMcpInstanceAdminTab } from '@/app/(main)/workspace/mcp-servers/team/components/mcp-instance-admin-extras';
export type { McpInstanceAdminProps } from '@/app/(main)/workspace/mcp-servers/team/components/mcp-instance-admin-extras';
// An action the skills page adds beside its own, with its panel (OSS: none)
export { useSkillsPageAction } from '@/app/(main)/workspace/skills/personal/components/skills-page-extras';
// Why agents can't use an imported skill, in an edition's own words for its review states (OSS: the card's own)
export { skillReviewNoticeKey } from '@/app/(main)/workspace/skills/personal/components/skills-page-extras';
export type { SkillsPageAction } from '@/app/(main)/workspace/skills/personal/components/skills-page-extras';
