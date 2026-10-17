export const PROJECT_NAME_MAX_LENGTH = 100;
export const PROJECT_DESCRIPTION_MAX_LENGTH = 1000;
export const PROJECT_INSTRUCTIONS_MAX_LENGTH = 8000;

export const PROJECT_VISIBILITY_VALUES = ['private', 'org'] as const;
export const PROJECT_CHAT_SHARING_VALUES = ['private', 'members'] as const;
export const PROJECT_MEMBER_ROLE_VALUES = ['viewer', 'editor'] as const;
export const PROJECT_PRINCIPAL_TYPE_VALUES = ['user', 'team'] as const;

/** Max members that can be added/updated in a single PUT /:projectId/members request. */
export const PROJECT_MEMBERS_BATCH_MAX = 50;

export const DEFAULT_PROJECT_VISIBILITY = 'private' as const;
export const DEFAULT_PROJECT_CHAT_SHARING = 'private' as const;

/** Conversation-level override of a project's default chat sharing. Mirrors `chatSessions.projectVisibility`. */
export const PROJECT_CHAT_VISIBILITY_VALUES = ['private', 'project'] as const;
