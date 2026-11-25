/**
 * The Sandbox group of built-in /commands (SLASH3): each reads or changes the
 * chat's workspace store, or its settings, through the owner-only session
 * routes; only `/run` starts a sandbox. A row is never sent to a model.
 */
import type { TFunction } from 'i18next';
import { create } from 'zustand';
import { downloadPreviewFile } from '@/app/components/file-preview/utils';
import { ChatApi, statusOf } from './api';
import { useBuiltinResults, useWorkspaceHeads, type BuiltinResult, type WorkspaceSession } from './builtin-results';
import { engineName } from '@/app/(main)/workspace/ai-accounts/access-copy';
import { useActivityPanelStore } from './components/activity-panel/activity-panel-store';
import { followDurableRunInSlot } from './streaming';
import { useChatStore } from './store';
import {
  buildStreamRequestModeFields,
  streamChatModeToAgentApiChatMode,
  type DurableAgentRunHandle,
  type ModelOverride,
  type SandboxCommandRequest,
  type SessionSandboxView,
} from './types';
import { safeFileStem } from './utils/file-name';

/** Text `/open` shows inline; a longer file is cut with a note (SLASH3 §3.2). */
export const OPEN_TEXT_MAX_BYTES = 256 * 1024;
/** An image larger than this is offered as a download instead. */
export const OPEN_IMAGE_MAX_BYTES = 10 * 1024 * 1024;
export const OPEN_MAX_LINES = 400;
export const OPEN_CSV_ROWS = 50;

const IMAGE_EXTENSIONS = /\.(png|jpe?g|gif|webp)$/i;

export interface SandboxRow {
  rowKey: string;
  afterPairs: number;
  typed: string;
}

/** The chat's workspace path a typed argument names: no leading `./` or `/`, no trailing slash. */
export function workspaceArg(args: string): string {
  return args.trim().replace(/^(\.\/|\/)+/, '').replace(/\/+$/, '');
}

const basename = (path: string): string => path.split('/').pop() || path;

async function inRow(row: SandboxRow, work: () => Promise<BuiltinResult>): Promise<void> {
  const results = useBuiltinResults.getState();
  const id = results.add(row.rowKey, { typed: row.typed, afterPairs: row.afterPairs, result: { kind: 'loading' } });
  let result: BuiltinResult;
  try {
    result = await work();
  } catch {
    result = { kind: 'failed' };
  }
  settle(row.rowKey, id, result);
}

/**
 * A row's result in place; an upload's row takes its revision's id, so reading the chat's versions again on
 * reload (or on coming back to it) replaces it instead of showing it twice.
 */
function settle(rowKey: string, id: string, result: BuiltinResult): void {
  const results = useBuiltinResults.getState();
  if (result.kind !== 'uploaded') {
    results.update(rowKey, id, result);
    return;
  }
  const row = results.rows[rowKey]?.find((r) => r.id === id);
  if (row) results.replace(rowKey, id, { ...row, id: `upload-revision-${String(result.revision)}`, result });
}

/** `/files [path]`: the head's tree under `path`. */
export function showFiles(session: WorkspaceSession, path: string, row: SandboxRow, t: TFunction, running: boolean): Promise<void> {
  return inRow(row, async () => {
    try {
      const list = await ChatApi.listWorkspaceFiles(session, path || undefined);
      return { kind: 'files', list, session, running };
    } catch (error) {
      if (statusOf(error) === 404) return { kind: 'note', text: t('slashCommands.result.files.notFound', { path }) };
      throw error;
    }
  });
}

/** `/open <path>`: a preview from the file's first bytes (the whole file only for an image). */
export function openFile(session: WorkspaceSession, path: string, row: SandboxRow, t: TFunction, running: boolean): Promise<void> {
  return inRow(row, async () => {
    const read = await ChatApi.readWorkspaceFile(session, path, {
      maxBytes: IMAGE_EXTENSIONS.test(path) ? OPEN_IMAGE_MAX_BYTES : OPEN_TEXT_MAX_BYTES,
    });
    if ('refused' in read) {
      return read.refused === 404
        ? { kind: 'note', text: t('slashCommands.result.files.notFound', { path }) }
        : { kind: 'failed' };
    }
    return { kind: 'open', path, read, session, running };
  });
}

/** `/download [path]`: a file as itself, a folder or everything as a zip; audited by the server. */
export async function download(
  session: WorkspaceSession,
  path: string,
  options: { folder: boolean; title: string },
  row: SandboxRow,
  t: TFunction,
): Promise<void> {
  await inRow(row, async () => {
    if (path && !options.folder) {
      const file = await ChatApi.readWorkspaceFile(session, path, { download: true });
      // Not a file: try it as a folder.
      if ('refused' in file && file.refused === 404) return zip(session, path, options.title, t);
      if ('refused' in file) return { kind: 'failed' };
      downloadPreviewFile({ name: basename(path), blob: file.blob });
      return { kind: 'note', text: t('slashCommands.result.download.file', { name: basename(path) }) };
    }
    return zip(session, path, options.title, t);
  });
}

async function zip(session: WorkspaceSession, path: string, title: string, t: TFunction): Promise<BuiltinResult> {
  const archive = await ChatApi.downloadWorkspaceArchive(session, path || undefined);
  if ('refused' in archive) {
    if (archive.refused === 413) return { kind: 'note', text: t('slashCommands.result.download.tooLarge') };
    if (archive.refused === 404) return { kind: 'note', text: t('slashCommands.result.files.notFound', { path }) };
    return { kind: 'failed' };
  }
  const stem = safeFileStem(path ? basename(path) : title, { maxChars: 40, fallback: 'workspace', atWord: true });
  const name = `${stem}-v${String(archive.revision)}.zip`;
  downloadPreviewFile({ name, blob: archive.blob });
  return { kind: 'note', text: t('slashCommands.result.download.zip', { name }) };
}

/** `/sandbox`: the card, with the last run's executor read from its pin or timeline as `/status` does. */
export function showSandbox(
  session: WorkspaceSession,
  run: DurableAgentRunHandle | null,
  row: SandboxRow,
  executorOf: (run: DurableAgentRunHandle) => Promise<string | null>,
): Promise<void> {
  return inRow(row, async () => {
    const [view, executor] = await Promise.all([
      ChatApi.getSessionSandbox(session),
      run ? executorOf(run).catch(() => null) : Promise.resolve(null),
    ]);
    return { kind: 'sandbox', view, executor, session };
  });
}

/** "End now" on the card: asks first (`confirm`), then revokes; the row says what happened. */
export async function endKeptSandbox(rowKey: string, rowId: string, confirmed: boolean): Promise<void> {
  const row = useBuiltinResults.getState().rows[rowKey]?.find((r) => r.id === rowId);
  if (row?.result.kind !== 'sandbox' || row.result.kept === 'busy' || row.result.kept === 'ended') return;
  const result = row.result;
  const set = (kept: typeof result.kept) => useBuiltinResults.getState().update(rowKey, rowId, { ...result, kept });
  if (!confirmed) {
    set('confirm');
    return;
  }
  set('busy');
  try {
    await ChatApi.endKeptSandbox(result.session);
    set('ended');
  } catch {
    set('failed');
  }
}

/** `/logs`: the Activity drawer at Commands for the chat's last run, or a note when nothing ran. */
export function openLogs(run: DurableAgentRunHandle | null, mobile: boolean, row: SandboxRow, t: TFunction): void {
  if (!run) {
    useBuiltinResults.getState().add(row.rowKey, {
      typed: row.typed,
      afterPairs: row.afterPairs,
      result: { kind: 'note', text: t('slashCommands.result.logs.none') },
    });
    return;
  }
  useActivityPanelStore.getState().openRun(run, mobile, 'commands');
}

/** CSV's first rows as cells: quoted fields with commas and doubled quotes, one record per line. */
export function csvRows(text: string, limit: number): { rows: string[][]; more: boolean } {
  const rows: string[][] = [];
  const lines = text.split(/\r?\n/);
  for (const line of lines) {
    if (rows.length === limit) return { rows, more: true };
    if (line === '' && rows.length > 0) continue;
    const cells: string[] = [];
    let cell = '';
    let quoted = false;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (quoted) {
        if (ch === '"' && line[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else if (ch === '"') quoted = false;
        else cell += ch;
      } else if (ch === '"') quoted = true;
      else if (ch === ',') {
        cells.push(cell);
        cell = '';
      } else cell += ch;
    }
    cells.push(cell);
    rows.push(cells);
  }
  return { rows, more: false };
}

/** `/run --timeout 300 <command>`: the timeout run_code allows at most (SLASH3 §3.5). */
export const RUN_MAX_TIMEOUT_S = 600;
const TIMEOUT_FLAG = /^--timeout\s+(\d+)\s+/;

/** The line and its timeout from what follows `/run`. */
export function runArgs(args: string): { line: string; timeoutS?: number } {
  const trimmed = args.trim();
  const flag = TIMEOUT_FLAG.exec(trimmed);
  if (!flag) return { line: trimmed };
  return {
    line: trimmed.slice(flag[0].length).trim(),
    timeoutS: Math.min(RUN_MAX_TIMEOUT_S, Math.max(1, Number(flag[1]))),
  };
}

const INTERACTIVE = /^(?:sudo\s+)?(?:vim?|nvim|nano|emacs|top|htop|less|more|man|ssh|watch)(?:\s|$)/;
const INSTALL = /^(?:sudo\s+)?(?:pip3?|npm|yarn|apt(?:-get)?|brew)\s+install(?:\s|$)/;

/** A one-line hint instead of a run for what cannot work in one non-interactive call (§7 Risks); null: run it. */
export function runHintKey(line: string): string | null {
  if (INTERACTIVE.test(line)) return 'slashCommands.result.run.interactive';
  if (INSTALL.test(line)) return 'slashCommands.result.run.install';
  return null;
}

/** The refusal of a `/run` in words: the code or HTTP status the API gave. */
export function runRefusalText(t: TFunction, refused: string, retryAfterS?: number): string {
  if (refused.endsWith('RUN_ACTIVE')) return t('slashCommands.reason.wait');
  if (refused.endsWith('QUOTA_EXCEEDED') || refused === 'HTTP_429' || refused === 'TOO_MANY_REQUESTS') {
    return t('slashCommands.result.run.rateLimited', { seconds: retryAfterS ?? 60 });
  }
  if (refused === 'HTTP_404') return t('slashCommands.reason.sandboxOwner');
  return t('slashCommands.result.failed');
}

/**
 * `/run <command>`: admits the command run, then shows it as the chat's turn on its stream, so its output, an
 * approval card the agent's rules ask for, and Stop are the turn's own. Never sent to a model (SD9).
 */
export async function runCommand(
  session: WorkspaceSession,
  args: string,
  turn: Omit<SandboxCommandRequest, 'command' | 'idempotencyKey' | 'timeoutS'>,
  slotId: string,
  row: SandboxRow,
  t: TFunction,
): Promise<void> {
  const { line, timeoutS } = runArgs(args);
  if (!line) return;
  const note = (text: string) =>
    useBuiltinResults.getState().add(row.rowKey, { typed: row.typed, afterPairs: row.afterPairs, result: { kind: 'note', text } });
  const hint = runHintKey(line);
  if (hint !== null) {
    note(t(hint));
    return;
  }
  const answer = await ChatApi.runSandboxCommand(session, {
    ...turn,
    command: line,
    ...(timeoutS !== undefined ? { timeoutS } : {}),
    idempotencyKey: runIdempotencyKey(),
  });
  if ('refused' in answer) {
    note(runRefusalText(t, answer.refused, answer.retryAfterS));
    return;
  }
  await followDurableRunInSlot(slotId, session.agentId, session.sessionId, answer.runId, `${RUN_PREFIX}${line}`);
}

function runIdempotencyKey(): string {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? `slash-run-${crypto.randomUUID()}`
    : `slash-run-${String(Date.now())}-${Math.random().toString(36).slice(2, 10)}`;
}

/** How a `/run` turn's question reads (the server stores the same query). */
export const RUN_PREFIX = '/run ';

/** A sent `/run` turn's line, or null: the answer of such a turn gets its actions (SB3b). */
export function sentRunLine(question: string): string | null {
  return question.startsWith(RUN_PREFIX) ? question.slice(RUN_PREFIX.length) : null;
}

/**
 * The chat's mode, model and tools as its next message would send them (the regenerate request's own rules), so
 * admission grants a `/run` exactly what the agent's next turn would have (SD2).
 */
export function nextTurnFields(
  agentId: string | null,
  model: ModelOverride | null,
): Omit<SandboxCommandRequest, 'command' | 'idempotencyKey' | 'timeoutS'> {
  const state = useChatStore.getState();
  const strip = (key: string) => {
    const colon = key.indexOf(':');
    return colon >= 0 ? key.slice(colon + 1) : key;
  };
  const models = model
    ? {
        modelKey: model.modelKey,
        modelName: model.modelName || model.modelKey,
        modelFriendlyName: model.modelFriendlyName || model.modelName || model.modelKey,
        ...(model.modelProvider ? { modelProvider: model.modelProvider } : {}),
      }
    : {};
  if (agentId) {
    const { chatMode } = buildStreamRequestModeFields(state.settings, true);
    const selected = state.agentStreamTools;
    return {
      chatMode: streamChatModeToAgentApiChatMode(chatMode),
      ...models,
      ...(selected === null ? {} : { tools: [...new Set(selected.map(strip))] }),
      agentCapabilities: state.scopedAgentCapabilities[agentId] ?? { internalSearch: true, webSearch: true },
    };
  }
  const { chatMode } = buildStreamRequestModeFields(state.settings, false);
  if (state.settings.queryMode !== 'agent') return { chatMode: String(chatMode), ...models };
  const selected = state.universalAgentStreamTools ?? state.universalAgentToolCatalogFullNames;
  return {
    chatMode: String(chatMode),
    ...models,
    tools: [...new Set(selected.map(strip))],
    agentCapabilities: state.settings.agentCapabilities,
  };
}

/** How long a chat's `/sandbox` answer is trusted before the menu asks again. */
const SANDBOX_VIEW_TTL_MS = 10_000;

interface SandboxViewsState {
  views: Record<string, { view: SessionSandboxView | null; at: number }>;
  /** Read when `/` is typed (never on chat open): whether `/run` may run code here (O8). */
  load: (session: WorkspaceSession) => Promise<void>;
}

export const useSandboxViews = create<SandboxViewsState>((set, get) => ({
  views: {},
  load: async (session) => {
    const known = get().views[session.sessionId];
    if (known && Date.now() - known.at < SANDBOX_VIEW_TTL_MS) return;
    let view: SessionSandboxView | null = null;
    try {
      view = await ChatApi.getSessionSandbox(session);
    } catch {
      // Not the owner, or not a harness chat: the menu greys /run for that reason instead.
    }
    set((s) => ({ views: { ...s.views, [session.sessionId]: { view, at: Date.now() } } }));
  },
}));

/** `/run`'s grant as the menu shows it: none outside an agent turn; a rule's Deny; else granted (the server decides). */
export function runCodeOf(agentTurn: boolean, view: SessionSandboxView | null | undefined): 'granted' | 'missing' | 'denied' {
  if (!agentTurn) return 'missing';
  return view?.codeRuns === 'deny' ? 'denied' : 'granted';
}

/** Where `/upload` puts files when no folder is named (SD6); the server's default too. */
export const UPLOAD_DEFAULT_FOLDER = 'uploads';

/**
 * The browser's file picker, several files at once. Opened in the same task as the key press or click that asked
 * for it, so the browser allows it; resolves with nothing when the person closes it.
 */
export function pickFiles(): Promise<File[]> {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.addEventListener('change', () => resolve(Array.from(input.files ?? [])), { once: true });
    input.addEventListener('cancel', () => resolve([]), { once: true });
    input.click();
  });
}

const uploadKey = (): string =>
  typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? `slash-upload-${crypto.randomUUID()}`
    : `slash-upload-${String(Date.now())}-${Math.random().toString(36).slice(2, 10)}`;

/** The refusal of an upload in words, by the code or HTTP status the API gave. */
export function uploadRefusalKey(refused: string): string {
  if (refused.endsWith('WORKSPACE_WRITER_BUSY')) return 'slashCommands.reason.wait';
  if (refused.endsWith('WORKSPACE_CONFLICT')) return 'slashCommands.result.undo.moved';
  if (refused.endsWith('WORKSPACE_TOO_LARGE') || refused === 'HTTP_413') return 'slashCommands.result.upload.tooLarge';
  if (refused.endsWith('WORKSPACE_INVALID_CHANGE')) return 'slashCommands.result.upload.refusedPath';
  return UPLOAD_FAILED;
}

const UPLOAD_FAILED = 'slashCommands.result.upload.failed';
/** An upload that failed for no reason the API named: said as an upload, not as a read ("Could not load this"). */
const uploadFailed = (): BuiltinResult => ({ kind: 'uploadRefused', key: UPLOAD_FAILED });

/**
 * `/upload [folder]` once files are picked: files that would replace ones in the workspace ask first (SD6);
 * otherwise they become one revision at once. The row says what was added and offers Undo.
 */
export async function uploadFiles(
  session: WorkspaceSession,
  files: File[],
  folder: string,
  row: SandboxRow,
): Promise<void> {
  if (files.length === 0) return;
  await inRow(row, async () => {
    try {
      const head = await ChatApi.listWorkspaceFiles(session);
      const existing = new Set(head.files.map((f) => f.path));
      const replaced = files.map((f) => `${folder}/${f.webkitRelativePath || f.name}`).filter((path) => existing.has(path));
      if (replaced.length > 0) {
        return { kind: 'uploadConfirm', files, folder, replaced, baseRevision: head.revision, session };
      }
      return await send(session, files, folder, head.revision, false);
    } catch {
      return uploadFailed();
    }
  });
}

async function send(
  session: WorkspaceSession,
  files: File[],
  folder: string,
  baseRevision: number,
  replace: boolean,
): Promise<BuiltinResult> {
  const answer = await ChatApi.uploadWorkspaceFiles(session, {
    files,
    folder,
    baseRevision,
    idempotencyKey: uploadKey(),
    replace,
  });
  if ('refused' in answer) return { kind: 'uploadRefused', key: uploadRefusalKey(answer.refused) };
  // The menu greys /files, /open and /download on a head of 0; without this they stayed "no workspace yet" for
  // the head's cache lifetime after the chat's first upload.
  useWorkspaceHeads.getState().set(session.sessionId, answer.revision);
  return { kind: 'uploaded', revision: answer.revision, paths: answer.paths, session };
}

/** "Replace N files?" answered yes: the same files on the head the confirm showed. */
export async function confirmUpload(rowKey: string, rowId: string): Promise<void> {
  const row = useBuiltinResults.getState().rows[rowKey]?.find((r) => r.id === rowId);
  if (row?.result.kind !== 'uploadConfirm' || row.result.busy) return;
  const { files, folder, baseRevision, session } = row.result;
  useBuiltinResults.getState().update(rowKey, rowId, { ...row.result, busy: true });
  let result: BuiltinResult;
  try {
    result = await send(session, files, folder, baseRevision, true);
  } catch {
    result = uploadFailed();
  }
  settle(rowKey, rowId, result);
}

/** An agent's engine narrowing as kinds (`codex@1.2` → `codex`), once each: what `/engine` offers in its chat. */
export function engineKinds(profile: unknown): string[] {
  const engines = (profile as { engines?: unknown } | null | undefined)?.engines;
  if (!Array.isArray(engines)) return [];
  return [...new Set(engines.filter((e): e is string => typeof e === 'string').map((e) => e.split('@')[0] ?? e))];
}

/** `none` for "no engine", null for "as my default". */
export const ENGINE_NONE = 'none';

/** The person's coding-engine choice that means "the org's default" (ENGPICK). */
const ORG_DEFAULT_CHOICE = 'org_default';

/** What "As my default" runs: the person's own choice, else the org's default, else no engine. */
export function myDefaultEngine(view: { choice: string; orgDefault: string | null }): string {
  return view.choice === ORG_DEFAULT_CHOICE ? (view.orgDefault ?? ENGINE_NONE) : view.choice;
}

const comparable = (name: string): string => name.toLowerCase().replace(/[^a-z0-9]/g, '');
const DEFAULT_NAMES: ReadonlySet<string> = new Set(['default', 'mydefault', 'asmydefault']);

/**
 * The picker's choice a typed `/engine` name means (CMDUI): an engine's kind or the name the picker shows, in any
 * case and spacing (`Claude Code`, `claude_code`), or a unique start of one (`claude`); `default` for "As my
 * default". Only the picker's own choices: in an agent's chat, the engines its owner listed. undefined: none.
 */
export function findEngine(
  typed: string,
  picker: { options: ReadonlyArray<{ kind: string }>; none: boolean },
): string | null | undefined {
  const want = comparable(typed);
  if (!want) return undefined;
  if (DEFAULT_NAMES.has(want)) return null;
  const choices = [...picker.options.map((o) => o.kind), ...(picker.none ? [ENGINE_NONE] : [])];
  const names = (kind: string) => [comparable(kind), comparable(engineName(kind))];
  const exact = choices.find((kind) => names(kind).includes(want));
  if (exact !== undefined) return exact;
  const started = choices.filter((kind) => names(kind).some((n) => n.startsWith(want)));
  return started.length === 1 ? started[0] : undefined;
}

/**
 * `/engine [name]`: with a name, this chat's engine from its next message; without one, the picker with the
 * engines the org allows (each with why it cannot be used now), narrowed to the agent's own in an agent's chat.
 * In a new chat (`session` null) the choice is held for its first message (CMDUI2), which carries it to the server.
 */
export async function chooseEngine(
  session: WorkspaceSession | null,
  name: string,
  agentEngines: readonly string[],
  running: boolean,
  row: SandboxRow,
  newChatAgent: string | null = null,
): Promise<void> {
  await inRow(row, async () => {
    const { ModelAccountsApi } = await import('@/app/(main)/workspace/ai-accounts/api');
    const [view, current] = await Promise.all([
      ModelAccountsApi.codingEngine(),
      session ? ChatApi.getSessionSandbox(session).then((s) => s.codingEngine) : Promise.resolve(heldEngine(newChatAgent)),
    ]);
    const options = view.engines.filter((e) => agentEngines.length === 0 || agentEngines.includes(e.kind));
    const picker: Extract<BuiltinResult, { kind: 'engine' }> = {
      kind: 'engine',
      session,
      ...(session ? {} : { newChatAgent }),
      options,
      none: agentEngines.length === 0,
      current,
      myDefault: myDefaultEngine(view),
      running,
    };
    const typed = name.trim();
    if (!typed) return picker;
    const engine = findEngine(typed, picker);
    // A name that is no choice here is never sent: the server would refuse it as unusable, which it is not.
    if (engine === undefined) return { ...picker, unknown: typed };
    return saveEngine(picker, engine);
  });
}

/** The engine held for this new chat's first message, for its agent (null: the assistant); null when none is. */
function heldEngine(agentId: string | null): string | null {
  const held = useChatStore.getState().pendingCodingEngine;
  return held && held.agentId === agentId ? held.engine : null;
}

async function saveEngine(result: Extract<BuiltinResult, { kind: 'engine' }>, engine: string | null): Promise<BuiltinResult> {
  if (!result.session) {
    // Held, not saved: no chat exists yet. The first message names it, and the server checks and saves it then.
    const agentId = result.newChatAgent ?? null;
    useChatStore.getState().setPendingCodingEngine(engine === null ? null : { agentId, engine });
    return { ...result, current: engine, saved: true, refused: undefined, unknown: undefined, makeDefault: undefined, busy: false };
  }
  const answer = await ChatApi.setSessionCodingEngine(result.session, engine);
  if ('refused' in answer) return { ...result, refused: answer.refused, unknown: undefined, busy: false };
  return { ...result, current: answer.engine, saved: true, refused: undefined, unknown: undefined, makeDefault: undefined, busy: false };
}

/**
 * "Make this my default": the chat's engine becomes the person's own choice for new chats (the My AI accounts
 * select's save). The chat itself keeps its choice.
 */
export async function makeEngineMyDefault(rowKey: string, rowId: string): Promise<void> {
  const row = useBuiltinResults.getState().rows[rowKey]?.find((r) => r.id === rowId);
  if (row?.result.kind !== 'engine' || row.result.current === null || row.result.makeDefault === 'busy') return;
  const choice = row.result.current;
  useBuiltinResults.getState().update(rowKey, rowId, { ...row.result, makeDefault: 'busy' });
  const latest = () => useBuiltinResults.getState().rows[rowKey]?.find((r) => r.id === rowId)?.result;
  try {
    const { ModelAccountsApi } = await import('@/app/(main)/workspace/ai-accounts/api');
    const view = await ModelAccountsApi.putCodingEngine(choice);
    const now = latest();
    if (now?.kind === 'engine') useBuiltinResults.getState().update(rowKey, rowId, { ...now, makeDefault: 'saved', myDefault: myDefaultEngine(view) });
  } catch {
    const now = latest();
    if (now?.kind === 'engine') useBuiltinResults.getState().update(rowKey, rowId, { ...now, makeDefault: 'failed' });
  }
}

/** A choice in the picker: saved at once for the chat's next message; the row says it took. */
export async function pickEngine(rowKey: string, rowId: string, engine: string | null): Promise<void> {
  const row = useBuiltinResults.getState().rows[rowKey]?.find((r) => r.id === rowId);
  if (row?.result.kind !== 'engine' || row.result.busy) return;
  useBuiltinResults.getState().update(rowKey, rowId, { ...row.result, busy: true });
  let result: BuiltinResult;
  try {
    result = await saveEngine(row.result, engine);
  } catch {
    result = { ...row.result, busy: false, refused: 'failed' };
  }
  useBuiltinResults.getState().update(rowKey, rowId, result);
}
