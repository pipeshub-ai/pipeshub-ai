import type {
  InboxCounts,
  InboxFilter,
  InboxItem,
  InboxPreferences,
  InboxView,
  NoticeCursor,
} from '../domain/inbox';

/** The person whose Inbox a call reads or changes: always the route's verified caller. */
export interface InboxViewer {
  orgId: string;
  userId: string;
  isAdmin: boolean;
  /** The caller's own bearer credential, for sources another service decides (skill proposals, memory labels). */
  authorization?: string;
}

/**
 * A person's Inbox settings (ADR-INBOX-01): muted workflows, the pause, and which channels beyond the Inbox carry
 * their finished-run notices. One record per person; an absent one reads as `DEFAULT_INBOX_PREFERENCES`.
 */
export interface InboxPreferenceStore {
  preferences(orgId: string, userId: string): Promise<InboxPreferences>;
  /** Mutes or unmutes one schedule; at most `MAX_MUTED_SCHEDULES` stay muted (the oldest go first). */
  setMuted(
    orgId: string,
    userId: string,
    scheduleId: string,
    muted: boolean,
    now: number,
  ): Promise<InboxPreferences>;
  setPause(
    orgId: string,
    userId: string,
    pause: InboxPreferences['pause'],
    now: number,
  ): Promise<InboxPreferences>;
  setRunNotices(
    orgId: string,
    userId: string,
    channels: Partial<InboxPreferences['runNotices']>,
    now: number,
  ): Promise<InboxPreferences>;
}

/** A run's finished notice is decided once, whichever replica saw the run end (two may: a commit and a sweep). */
export interface RunEndNoticeClaims {
  /** True for the first claim of `runId`'s notice; false for every later one. */
  claimRunNotice(
    orgId: string,
    runId: string,
    userId: string,
    now: number,
  ): Promise<boolean>;
}

/**
 * Who is watching a conversation now (ADR-INBOX-01 §Watching): one lease per open stream, renewed while it is open,
 * shared by every Node replica (the run can end on a replica other than the one holding the stream).
 */
export interface RunWatchLeases {
  watch(lease: {
    orgId: string;
    userId: string;
    sessionId: string;
    watcherId: string;
    until: number;
  }): Promise<void>;
  unwatch(orgId: string, watcherId: string): Promise<void>;
  /** Whether a lease of the person's on the conversation is live at `at`. */
  watching(
    orgId: string,
    userId: string,
    sessionId: string,
    at: number,
  ): Promise<boolean>;
}

/** Where user and org deletion find the Inbox's eraser (CLEANUP-G1, G2). */
export const INBOX_ERASER = 'AgentHarnessInboxEraser';

/** A person's Inbox records, erased with them and with their org. */
export interface InboxEraser {
  eraseUser(orgId: string, userId: string): Promise<void>;
  eraseOrg(orgId: string): Promise<void>;
}

export type InboxStore = InboxPreferenceStore &
  RunEndNoticeClaims &
  RunWatchLeases &
  InboxEraser;

/** A page of the person's notices, newest first. */
export interface NoticePage {
  items: InboxItem[];
  /** Where the next page starts; absent on the last. */
  next?: NoticeCursor;
}

export type NoticeAction =
  'read' | 'unread' | 'archive' | 'unarchive' | 'restore';

/** Which of the person's notices an action changes. */
export type NoticeSelection =
  | { ids: readonly string[] }
  /** Every notice the action applies to: unread (`read`), read and not waiting (`archive`), archived (`unarchive`, `restore`). */
  | { all: true }
  /** Every unread finished-run notice of one conversation (the person opened it). */
  | { conversationId: string };

/**
 * The person's stored notices (main's notification repository, on either document store). Every call is the
 * caller's own: an id of anyone else's matches nothing.
 */
export interface InboxNotices {
  page(
    viewer: Pick<InboxViewer, 'orgId' | 'userId'>,
    query: {
      view: InboxView;
      filter: InboxFilter;
      cursor?: NoticeCursor;
      limit: number;
      muted: ReadonlySet<string>;
    },
  ): Promise<NoticePage>;
  unreadCount(viewer: Pick<InboxViewer, 'orgId' | 'userId'>): Promise<number>;
  /**
   * Deletes archived notices only (UIP3-FU-2): those named, or every archived one (up to the bulk bound); a notice
   * not archived, or not the caller's, is left alone. The notification ids it deleted.
   */
  removeArchived(
    viewer: Pick<InboxViewer, 'orgId' | 'userId'>,
    selection: { ids: readonly string[] } | { all: true },
  ): Promise<string[]>;
  /** The notification ids it changed. */
  apply(
    viewer: Pick<InboxViewer, 'orgId' | 'userId'>,
    action: NoticeAction,
    selection: NoticeSelection,
  ): Promise<string[]>;
}

/** One source of asks waiting on the person. A source that cannot answer is left out and named in `partial`. */
export interface InboxWaitingSource {
  /**
   * Answers only with the person's own credential (FIXMS1-FU-1): a count made without one (a push after a run
   * ended) leaves it out and says so, `recount`.
   */
  readonly credentialOnly?: boolean;
  readonly name: 'approvals' | 'questions' | 'skills' | 'reviews' | 'memories';
  /** `count`: only how many are needed (the badge); a source that audits what it shows records nothing then. */
  waiting(
    viewer: InboxViewer,
    limit: number,
    purpose?: 'feed' | 'count',
  ): Promise<InboxItem[]>;
}

/** Tells the person's open sessions their counts changed (the existing notification socket). */
export interface InboxCountPublisher {
  publish(userId: string, counts: InboxCounts): void;
}

/** A person's interactive run ended unseen (ADR-INBOX-01 §Finished runs): tell them, on the channels given. */
export interface InteractiveRunNotice {
  orgId: string;
  userId: string;
  sessionId: string;
  agentKey?: string;
  runId: string;
  /** `finished`: it answered; otherwise it couldn't finish. */
  finished: boolean;
  /** A finished run that stopped early and answered with what it had. */
  cutShort: boolean;
  /** The conversation's title; empty when it has none. */
  chatTitle: string;
  channels: ReadonlyArray<'in_app' | 'email' | 'slack'>;
  /** Paused: stored in the Inbox, not pushed. */
  quiet: boolean;
}

/** The channels a notice reaches a person on: the Inbox (stored, then pushed), email and the agent's Slack bot. */
export interface RunEndNotifier {
  runFinished(notice: InteractiveRunNotice): Promise<void>;
}
