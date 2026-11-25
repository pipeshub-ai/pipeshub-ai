/**
 * What a built-in /command showed, as rows in the conversation (SLASH2 §3).
 * Local to this browser and gone on reload (O6): what a command changed is
 * saved by the action it reused (a cancelled run, a renamed chat, the next
 * run's spec), never by these rows, and no row ever reaches a model.
 */
import { create } from 'zustand';
import { ChatApi } from './api';
import type { DurableAgentRunHandle, RunBudgetView, SessionSandboxView, WorkspaceDiff, WorkspaceFileList, WorkspaceFileRead } from './types';

export interface StatusView {
  model: string | null;
  effort: string | null;
  executor: string | null;
  /** A loop run's coding engine for its code tasks: a kind, `none`, or null when unknown (or an engine run). */
  codingEngine: string | null;
  tools: number | null;
  approvals: string | null;
  /** No approval rules to show (no pin): how many approvals the run asked instead. */
  approvalsAsked: number | null;
  runStatus: string | null;
  tokens: number | null;
  /** The run's policy_version (C4.04): its pin's digest, shortened; null without a pin. */
  policyVersion: string | null;
  /** The chat runs on the classic path: only its model and mode are known. */
  legacy: boolean;
  mode: string | null;
}

/** `effort` when the run pinned none: the model's own default. */
export const STATUS_EFFORT_DEFAULT = 'default';

export interface UsageView {
  chat: { runs: number; usdMicros: number; tokens: number } | null;
  last: RunBudgetView | null;
}

export interface PermissionsView {
  approve: boolean;
  deny: boolean;
  approvals: Record<string, string> | null;
}

export type BuiltinResult =
  | { kind: 'loading' }
  | { kind: 'failed' }
  | { kind: 'note'; text: string }
  | { kind: 'help' }
  | { kind: 'model'; model: string; was: string | null; running: boolean }
  | { kind: 'effort'; effort: string; running: boolean }
  | { kind: 'plan'; running: boolean }
  /** `/compact` saved on the conversation; the next run compacts, and its answer shows what that did. */
  | { kind: 'compact'; focus: string }
  | { kind: 'status'; status: StatusView; run: DurableAgentRunHandle | null }
  | { kind: 'usage'; usage: UsageView; run: DurableAgentRunHandle | null }
  | { kind: 'list'; list: 'skills' | 'mcp' | 'agents'; items: Array<{ id: string; name: string; detail?: string }> }
  | { kind: 'permissions'; permissions: PermissionsView; admin: boolean }
  /** `/diff`: local, gone on reload (O6). */
  | { kind: 'diff'; diff: WorkspaceDiff; session: WorkspaceSession }
  /** `/undo` before it runs: the last turn's files, waiting for a confirm. */
  | { kind: 'undoConfirm'; diff: WorkspaceDiff; session: WorkspaceSession; busy?: boolean; refused?: string }
  /** `/undo` done: rebuilt from the workspace's `rollback` revision on reload (O6). */
  | { kind: 'undone'; revision: number; restoredFrom?: number; files?: number }
  /** `/files` (SLASH3): the head's tree; `running`: a reply may have newer, unsaved changes. */
  | { kind: 'files'; list: WorkspaceFileList; session: WorkspaceSession; running: boolean }
  /** `/open` (SLASH3): one file's preview, built from its bytes in the browser (never served as a page). */
  | { kind: 'open'; path: string; read: WorkspaceFileRead; session: WorkspaceSession; running: boolean }
  /** `/upload` (SLASH3): files that would replace ones in the workspace, waiting for "Replace N files?". */
  | {
      kind: 'uploadConfirm';
      files: File[];
      folder: string;
      replaced: string[];
      baseRevision: number;
      session: WorkspaceSession;
      busy?: boolean;
    }
  /** `/upload` done: rebuilt from the workspace's `upload` revision on reload (O6), with its paths, and with Undo while it is the head. */
  | { kind: 'uploaded'; revision: number; paths?: string[]; session?: WorkspaceSession }
  | { kind: 'uploadRefused'; key: string }
  /** The engine a new chat's first message asked for did not hold (CMDUI2): that message ran on the default. */
  | { kind: 'engineNotSaved'; engine: string }
  /**
   * `/engine` (SLASH3): the chat's engine picker. `current` is the chat's own choice (null: the person's default);
   * `none`: "no engine" is offered (not in an agent's chat, whose owner set its engines). `myDefault` is what "As my
   * default" means now (an engine kind or `none`). `unknown`: the typed name matched none of the choices, so
   * nothing was saved. `makeDefault` steps through "Make this my default" (CMDUI). A null `session` is a new chat
   * (CMDUI2): `current` is the choice its first message carries, held for `newChatAgent` (null: the assistant).
   */
  | {
      kind: 'engine';
      session: WorkspaceSession | null;
      newChatAgent?: string | null;
      options: Array<{ kind: string; available: boolean; reason: string | null }>;
      none: boolean;
      current: string | null;
      myDefault: string;
      running: boolean;
      saved?: boolean;
      busy?: boolean;
      refused?: string;
      unknown?: string;
      makeDefault?: 'busy' | 'saved' | 'failed';
    }
  /**
   * `/sandbox` (SLASH3): what runs the chat's code. `executor` is the last run's; `kept` steps through "End now":
   * confirm, then ended (or refused).
   */
  | {
      kind: 'sandbox';
      view: SessionSandboxView;
      executor: string | null;
      session: WorkspaceSession;
      kept?: 'confirm' | 'busy' | 'ended' | 'failed';
    };

/** The chat whose workspace a `/diff` or `/undo` row acts on. */
export interface WorkspaceSession {
  agentId?: string;
  sessionId: string;
}

export interface BuiltinRow {
  id: string;
  /** What was typed, as the row's heading. */
  typed: string;
  /** How many question/answer pairs the chat showed when it was typed: the row sits after them. */
  afterPairs: number;
  result: BuiltinResult;
}

/** Rows of a chat with no slot yet (the empty "new chat" page). */
export const NEW_CHAT_KEY = 'new';

interface BuiltinResultsState {
  rows: Record<string, BuiltinRow[]>;
  /** How many question/answer pairs each chat shows now, as the message list counts them. */
  pairs: Record<string, number>;
  setPairs: (key: string, count: number) => void;
  add: (key: string, row: Omit<BuiltinRow, 'id'>) => string;
  update: (key: string, id: string, result: BuiltinResult) => void;
  /** Adds the row under its own id, or replaces the row that has it (a saved row read again on reload). */
  put: (key: string, row: BuiltinRow) => void;
  /** A live row settled under another id (its saved revision's): the row, and its reveal, move to `row.id`. */
  replace: (key: string, id: string, row: BuiltinRow) => void;
  dismiss: (key: string, id: string) => void;
  clear: (key: string) => void;
  /**
   * The row the person typed last, for the message list to bring into view: a result lands after the last reply,
   * under the composer, where nobody saw it. `seq` changes whenever the row is added or settles (its height changes).
   */
  reveal: BuiltinReveal | null;
}

export interface BuiltinReveal {
  key: string;
  id: string;
  seq: number;
}

let nextId = 0;

const nextReveal = (reveal: BuiltinReveal | null): number => (reveal?.seq ?? 0) + 1;
const isRevealed = (reveal: BuiltinReveal | null, key: string, id: string): boolean => reveal?.key === key && reveal.id === id;

function putRow(rows: BuiltinRow[], row: BuiltinRow): BuiltinRow[] {
  const at = rows.findIndex((r) => r.id === row.id);
  return at < 0 ? [...rows, row] : rows.map((r, i) => (i === at ? row : r));
}

export const useBuiltinResults = create<BuiltinResultsState>((set) => ({
  rows: {},
  pairs: {},
  setPairs: (key, count) => set((s) => (s.pairs[key] === count ? s : { pairs: { ...s.pairs, [key]: count } })),
  reveal: null,
  add: (key, row) => {
    nextId += 1;
    const id = `builtin-row-${nextId}`;
    set((s) => ({
      rows: { ...s.rows, [key]: [...(s.rows[key] ?? []), { ...row, id }] },
      reveal: { key, id, seq: nextReveal(s.reveal) },
    }));
    return id;
  },
  update: (key, id, result) =>
    set((s) => ({
      rows: { ...s.rows, [key]: (s.rows[key] ?? []).map((row) => (row.id === id ? { ...row, result } : row)) },
      ...(isRevealed(s.reveal, key, id) ? { reveal: { key, id, seq: nextReveal(s.reveal) } } : {}),
    })),
  put: (key, row) => set((s) => ({ rows: { ...s.rows, [key]: putRow(s.rows[key] ?? [], row) } })),
  replace: (key, id, row) =>
    set((s) => ({
      rows: { ...s.rows, [key]: putRow((s.rows[key] ?? []).filter((r) => r.id !== id), row) },
      ...(isRevealed(s.reveal, key, id) ? { reveal: { key, id: row.id, seq: nextReveal(s.reveal) } } : {}),
    })),
  dismiss: (key, id) =>
    set((s) => ({ rows: { ...s.rows, [key]: (s.rows[key] ?? []).filter((row) => row.id !== id) } })),
  clear: (key) =>
    set((s) => {
      const { [key]: _gone, ...rest } = s.rows;
      return { rows: rest };
    }),
}));

const EMPTY: BuiltinRow[] = [];

/** The rows of one chat, stable when it has none. */
export const useBuiltinRows = (key: string): BuiltinRow[] => useBuiltinResults((s) => s.rows[key] ?? EMPTY);

/** How long a chat's workspace head is trusted before the menu asks again. */
const WORKSPACE_HEAD_TTL_MS = 10_000;

interface WorkspaceHeadsState {
  /** The head revision per chat; 0: no files yet. */
  heads: Record<string, { head: number; at: number }>;
  load: (session: WorkspaceSession) => Promise<void>;
  set: (sessionId: string, head: number) => void;
}

/**
 * Whether each chat has a workspace, for greying `/diff`, `/undo` and `/review` (O8). Read when `/` is typed,
 * never on chat open: one small request, owner only (anyone else reads as no workspace).
 */
export const useWorkspaceHeads = create<WorkspaceHeadsState>((set, get) => ({
  heads: {},
  load: async (session) => {
    const known = get().heads[session.sessionId];
    if (known && Date.now() - known.at < WORKSPACE_HEAD_TTL_MS) return;
    let head = 0;
    try {
      head = (await ChatApi.listWorkspaceVersions(session, { limit: 1 })).head;
    } catch {
      // Not the owner, or not a harness chat: no workspace to act on.
    }
    get().set(session.sessionId, head);
  },
  set: (sessionId, head) => set((s) => ({ heads: { ...s.heads, [sessionId]: { head, at: Date.now() } } })),
}));
