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
