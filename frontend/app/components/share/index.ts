'use client';

// Share components barrel export
export { ShareSidebar } from './share-sidebar';
export { ShareHeaderGroup } from './share-header-group';
export { SharedAvatarStack } from './shared-avatar-stack';
export { ShareButton } from './share-button';
export { ShareableRow } from './shareable-row';
export { RoleDropdownMenu } from './role-dropdown-menu';
export { ShareSearchInput } from './share-search-input';
export { ShareSettings } from './share-settings';
export { ShareCommonApi } from './api';

// Type exports
export type {
  ShareAdapter,
  SharedMember,
  ShareRole,
  ShareRoleOption,
  ShareSettingDescriptor,
  ShareConfirmSpec,
  ShareMode,
  ShareAccessSummary,
  ShareEntityType,
  ShareSelection,
  ShareSubmission,
  ShareSubmissionPrincipal,
  ShareTeam,
  ShareUser,
  SharedAvatarMember,
} from './types';
export {
  getShareRoleLabels,
  buildShareSubmission,
  toTeamShareRole,
  TEAM_SHARE_ROLES,
  DEFAULT_TEAM_SHARE_ROLE,
} from './types';
