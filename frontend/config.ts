'use client';

export {
  useAuthStore,
  type User,
  logoutAndRedirect,
  logoutFromWorkspaceMenu,
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
export { AgentChatHeader } from '@/app/(main)/chat/components/agent-chat-header';
export { OAuthAppSelector } from '@/app/(main)/workspace/connectors/components/authenticate-tab/oauth-app-selector';
// Users page header — Requests + Blocked Users (Admin Org) between search and Invite
export { UsersPageHeaderActions } from '@/app/(main)/workspace/users/components/users-page-header-actions';
export { isMcpInstanceReadOnly, McpInheritedBadge, McpInheritedCallout } from '@/app/(main)/workspace/mcp-servers/team/components/mcp-inheritance-guards';
export { useUserPermission } from '@/lib/hooks/use-user-permission';
export { PermissionLockIcon } from '@/app/components/ui/permission-lock-icon';
export { PermissionDeniedDialog, usePermissionDeniedDialog } from '@/app/components/ui/permission-denied-dialog';
export { InheritedConfigNotice } from '@/app/(main)/workspace/components/inherited-config-notice';


