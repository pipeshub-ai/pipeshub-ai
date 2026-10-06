import { AccessLevel, ConversationSettings } from './types';
import { CollabErrorCode } from './errors';

export type CollaboratorState = 'active' | 'former_member' | 'deleted_team';

export interface OwnerDto {
  userId: string;
  displayName: string;
}

export interface CollaboratorDto {
  principalType: 'user' | 'team';
  principalId: string;
  displayName: string;
  accessLevel: AccessLevel;
  state: CollaboratorState;
  addedBy?: string;
  addedAt?: string;
}

/** The owner's (and an inviting editor's) view. */
export interface CollaboratorsView {
  owner: OwnerDto;
  collaborators: CollaboratorDto[];
  collaboratorCount: number;
  settings: Required<Omit<ConversationSettings, 'respondMode'>> &
    Pick<ConversationSettings, 'respondMode'>;
}

/** Everyone else: no identities of other recipients. */
export interface CollaboratorsSummary {
  owner: OwnerDto;
  collaboratorCount: number;
  myAccess: AccessLevel | 'owner';
  /** Present once the owner has set it; the composer needs it to tell a note from a question. */
  respondMode?: ConversationSettings['respondMode'];
}

export type CollaborationListResponse =
  | CollaboratorsView
  | CollaboratorsSummary;

export interface FeedAuthor {
  userId: string;
  displayName: string;
}

export interface FeedActiveRun {
  userId: string;
  displayName: string;
  startedAt: string;
  /** Only for callers who can write. */
  runId?: string;
}

export interface FeedMessage {
  _id: string;
  seq: number;
  messageType: string;
  content: string;
  author?: FeedAuthor;
  /** Only the caller's own latest vote. */
  feedback?: unknown[];
  [field: string]: unknown;
}

export interface FeedResponse {
  messages: FeedMessage[];
  rev: number;
  /** The `seq` to ask for next. */
  nextSeq: number;
  hasMore: boolean;
  activeRun: FeedActiveRun | null;
  lastActivityAt: number;
}

/** The 51 section 9 codes that can block a send, plus the agent being gone. */
export type ReadinessReason =
  | Extract<
      CollabErrorCode,
      | 'CONVERSATION_READ_ONLY'
      | 'OWNER_INACTIVE'
      | 'PROJECT_ACCESS_REQUIRED'
      | 'CONNECTOR_SETUP_REQUIRED'
    >
  | 'AGENT_UNAVAILABLE';

export interface Readiness {
  canSend: boolean;
  reasons: ReadinessReason[];
  /** The caller's own missing toolsets only. */
  missingToolsets?: string[];
}
