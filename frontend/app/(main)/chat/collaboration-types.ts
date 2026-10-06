import type { ConversationMessage } from './types';

export type ConversationRole = 'owner' | 'write' | 'read';

/** What the server says the caller may do in one conversation. The UI never derives more than this. */
export interface AccessView {
  role: ConversationRole;
  isOwner: boolean;
  accessLevel: ConversationRole;
  canSend: boolean;
  canManage: boolean;
  canInvite: boolean;
  isCollaborative: boolean;
}

/**
 * `access` as detail and list rows carry it. With the flag off the server sends only the legacy
 * `{isOwner, accessLevel}` pair, so every other field may be missing.
 */
export type ApiAccess = Pick<AccessView, 'isOwner'> &
  Partial<Omit<AccessView, 'isOwner' | 'accessLevel'>> & { accessLevel?: string };

export interface MessageAuthor {
  userId: string;
  /** `null` for a person who has left the organization. */
  displayName: string | null;
}

export interface ActiveRunDto {
  /** Only sent to callers who can write. */
  runId?: string;
  userId: string;
  displayName: string;
  startedAt: string;
}

export type CollaboratorPrincipalType = 'user' | 'team';
export type CollaboratorAccessLevel = 'read' | 'write';

export interface CollaboratorDto {
  principalType: CollaboratorPrincipalType;
  principalId: string;
  displayName: string;
  accessLevel: CollaboratorAccessLevel;
  state: 'active' | 'former_member' | 'deleted_team';
  addedBy?: string;
  addedAt?: string;
}

export interface CollaboratorRequest {
  principalType: CollaboratorPrincipalType;
  principalId: string;
  accessLevel: CollaboratorAccessLevel;
}

export interface CollaborationOwner {
  userId: string;
  displayName: string;
}

export interface CollaborationSettings {
  editorsCanInvite: boolean;
  ownerContentShared: boolean;
  /** Absent means `smart`. */
  respondMode?: 'smart' | 'mention_only' | 'always';
}

/** What the owner, and an editor allowed to invite, get. */
export interface CollaboratorsView {
  owner: CollaborationOwner;
  collaborators: CollaboratorDto[];
  collaboratorCount: number;
  settings: CollaborationSettings;
}

/** What everyone else gets: no other person or team is named. */
export interface CollaboratorsSummary {
  owner: CollaborationOwner;
  collaboratorCount: number;
  myAccess: ConversationRole;
  respondMode?: 'smart' | 'mention_only' | 'always';
}

export type CollaboratorsResponse = CollaboratorsView | CollaboratorsSummary;

export function isCollaboratorsView(r: CollaboratorsResponse): r is CollaboratorsView {
  return 'collaborators' in r;
}

export interface PutCollaboratorsInput {
  collaborators: CollaboratorRequest[];
  /** Up to 500 characters; the recipient sees the first 140. */
  note?: string;
  confirmOrgWide?: true;
}

export type ConversationRef =
  | { kind: 'chat'; id: string }
  | { kind: 'agent'; agentKey: string; id: string };

/** A stored message plus the collaboration fields a feed row carries. */
export interface FeedMessage extends ConversationMessage {
  seq: number;
  author?: MessageAuthor | null;
}

export interface FeedPage {
  messages: FeedMessage[];
  rev: number;
  nextSeq: number;
  hasMore: boolean;
  activeRun: ActiveRunDto | null;
  /** Milliseconds since the epoch. */
  lastActivityAt: number;
}

export const FEED_NOT_MODIFIED = 'not-modified' as const;
export type FeedResult = FeedPage | typeof FEED_NOT_MODIFIED;

export type ReadinessReason =
  | 'CONVERSATION_READ_ONLY'
  | 'OWNER_INACTIVE'
  | 'PROJECT_ACCESS_REQUIRED'
  | 'CONNECTOR_SETUP_REQUIRED'
  | 'AGENT_UNAVAILABLE';

export interface Readiness {
  canSend: boolean;
  reasons: ReadinessReason[];
  missingToolsets?: string[];
}

export type ExplainRole = 'viewer' | 'editor' | 'owner';

export interface ExplainPath {
  type: 'owner' | 'direct' | 'team' | 'project';
  /** `null` for a team the caller is not in. */
  ref: string | null;
  role: ExplainRole;
}

export interface ExplainResponse {
  role: ExplainRole | 'none';
  via: ExplainPath[];
}

export type AccessChange =
  | { type: 'link'; projectId: string }
  | { type: 'unlink' }
  | { type: 'visibility'; visibility: 'private' | 'project' };

export interface PrincipalRole {
  userId?: string;
  teamId?: string;
  role: ExplainRole;
}

export interface AccessChangePreview {
  gains: PrincipalRole[];
  loses: PrincipalRole[];
  becomesReadOnly: PrincipalRole[];
  truncated: boolean;
}

/** Codes from 51 §9 and the OpenAPI `ConversationErrorCode`, plus `AGENT_UNAVAILABLE` (readiness). */
export const CONVERSATION_ERROR_CODES = [
  'CONVERSATION_NOT_FOUND',
  'CONVERSATION_READ_ONLY',
  'CONVERSATION_OWNER_ONLY',
  'REGENERATE_NOT_ALLOWED',
  'RESUME_NOT_ALLOWED',
  'OWNER_INACTIVE',
  'OWNER_STATUS_UNAVAILABLE',
  'PROJECT_ACCESS_REQUIRED',
  'TEAM_RESOLUTION_UNAVAILABLE',
  'CONVERSATION_BUSY',
  'CONVERSATION_CHANGED',
  'DUPLICATE_MESSAGE',
  'RUN_LOST',
  'COLLABORATOR_LIMIT',
  'MUTED_SESSIONS_LIMIT',
  'CONNECTOR_SETUP_REQUIRED',
  'INVALID_PRINCIPAL',
  'RATE_LIMITED',
  'ORG_WIDE_CONFIRMATION_REQUIRED',
  'AGENT_UNAVAILABLE',
  'MENTION_NOT_ALLOWED',
  'MENTION_SA_AGENT_SHARED',
  'MENTION_DIRECTORY_UNAVAILABLE',
  'MESSAGE_IS_NOTE',
  'MESSAGE_NOT_NOTE',
] as const;

export type ConversationErrorCode = (typeof CONVERSATION_ERROR_CODES)[number];

export function isConversationErrorCode(code: unknown): code is ConversationErrorCode {
  return typeof code === 'string' && (CONVERSATION_ERROR_CODES as readonly string[]).includes(code);
}
