/**
 * The label of an answer: everything the model calls behind it were given. The AI backend builds it; Node stores
 * it on the answer row, bound to the stored version of the text, and never returns it to a client.
 */

export const LABEL_SCHEMA_VERSION = 5;

/** The version id read for a row written before labels existed. */
export const LEGACY_LABEL_VER = 'legacy';

/** How many earlier versions of an answer keep their label. */
export const LABEL_HISTORY_MAX = 20;

/** Every kind of source a label lists, in the order the AI backend writes them. */
export const LABEL_DIRECT_KEYS = [
  'records',
  'vrids',
  'nodes',
  'saVrids',
  'personal',
  'agents',
  'chat',
  'projects',
  'marks',
] as const;

export type LabelDirectKey = (typeof LABEL_DIRECT_KEYS)[number];

export interface LabelAgent {
  key: string;
  kind: 'service' | 'config';
  /** The run was given the text of a skill assigned to this agent that its asker can't open. */
  skill?: true;
}

/** A file attached in a chat, by the chat it was attached in. */
export interface LabelUpload {
  conversationId: string;
  recordId: string;
}

/** An administrator's statement about a source (a self-hosted search that reaches only the internet), at a version. */
export interface LabelMark {
  source: string;
  ver: string | number;
}

export interface LabelDirect {
  /** Records the run was shown, by record id: what a reader must be able to open. */
  records: string[];
  /** The same records by virtual record id; kept for audit, never checked. */
  vrids: string[];
  /** Folders, record groups, knowledge bases, apps and selected filter nodes. */
  nodes: string[];
  /** Shown while searching as a service identity; audit only. */
  saVrids: string[];
  /** People whose personal-credential tool results reached the run. */
  personal: string[];
  agents: LabelAgent[];
  chat: LabelUpload[];
  projects: string[];
  marks: LabelMark[];
}

export const LABEL_PARENT_MODES = [
  'text',
  'full',
  'grant',
  'preswitch',
] as const;

export type LabelParentMode = (typeof LABEL_PARENT_MODES)[number];

/** An earlier row version the run was given. */
export interface LabelParent {
  id: string;
  ver: string;
  mode: LabelParentMode;
  /** A file the run read, with `id` and `ver` empty: its version is the one the run that wrote it wrote. */
  artifactId?: string;
  /** For a file, that run. A file parent without it can't be judged, so only the asker's own read passes. */
  runId?: string;
  /**
   * For a file whose own record names no run (written by an older release, or outside a run): its chat, and when it
   * was last written (epoch ms). Written before the rules started for that chat, it is judged as an answer from
   * before labels; without both, as a file with no run.
   */
  conversationId?: string;
  writtenAt?: number;
}

export interface AnswerProvenance {
  v: typeof LABEL_SCHEMA_VERSION;
  /** The `labelVer` of the row version this label belongs to. Set by Node when the row is stored. */
  ver: string;
  /** sha256 of the stored text and steps of that version. Set by Node when the row is stored. */
  contentHash: string;
  direct: LabelDirect;
  parents: LabelParent[];
  complete: boolean;
  /** Which set of label writers built it. */
  tagset: number;
  /** Nothing in it restricts who may read the answer. */
  pub: boolean;
  /** Why the label is incomplete. */
  incomplete?: string[];
}

/** The label of an earlier version of a row, kept whole. */
export interface LabelHistoryEntry {
  ver: string;
  runId?: string;
  provenance?: AnswerProvenance;
  /** Set when the version was removed rather than replaced. */
  retiredAt?: Date;
  /** The share of that version, kept with it: what was built on it is still judged by it. */
  consent?: {
    by: unknown;
    at: Date | string;
    ver: string;
    scope: 'everyone' | 'people';
    how?: string;
    agents?: string[];
    on?: Array<{ row: string; ver: string }>;
  };
  /** Who a share with people of that version covered. */
  consentTo?: Array<{ userId: unknown; memberSince: string }>;
}

/**
 * Row fields that hold label data: the label, its archive, and the fields stored beside it about who an answer was
 * shared with, who asked for it, who passed its check and, on rows stored while an earlier release's suggestions ran (since
 * taken out), whom it suggested its asker mention or share it with. None of them is ever returned to a client.
 */
export const LABEL_ROW_FIELDS = [
  'provenance',
  'labelHistory',
  'audience',
  'consentTo',
  'shareRequests',
  'suggestions',
] as const;
