/**
 * What each built-in /command does (SLASH2 §2.2). Every one calls the API or
 * action its button already calls, with the caller's own token: a command
 * never does more than the same click in the UI. `/model`, `/effort` and
 * `/plan` change only what the next message is sent with; a running reply
 * keeps its pins (§2.3).
 */
import type { TFunction } from 'i18next';
import { useCommandStore } from '@/lib/store/command-store';
import { toast } from '@/lib/store/toast-store';
import { ChatApi } from './api';
import { activityRun } from './activity';
import { BUILTIN_BUS, type BuiltinCommand } from './builtin-commands';
import {
  STATUS_EFFORT_DEFAULT,
  useBuiltinResults,
  useWorkspaceHeads,
  type BuiltinResult,
  type StatusView,
  type WorkspaceSession,
} from './builtin-results';
import { exportConversation } from './utils/export-conversation';
import {
  chooseEngine,
  download,
  ENGINE_NONE,
  nextTurnFields,
  openFile,
  openLogs,
  pickFiles,
  runCommand,
  showFiles,
  showSandbox,
  UPLOAD_DEFAULT_FOLDER,
  uploadFiles,
  workspaceArg,
} from './sandbox-commands';
import { useChatStore } from './store';
import { useRepositoryStore } from './repository-store';
import { REASONING_EFFORT_OPTIONS } from './components/chat-panel/expansion-panels/model-selector/model-selector-panel';
import type { AgentStrategy, AvailableLlmModel, DurableAgentRunHandle, ModelOverride, ReasoningEffort, ResolvedRunView, RunTimelineView } from './types';

type Slot = Parameters<typeof activityRun>[0];

export interface BuiltinDeps {
  t: TFunction;
  /** Where its row goes: the slot's id, or `NEW_CHAT_KEY`. */
  rowKey: string;
  afterPairs: number;
  slot: Slot | undefined;
  agentId: string | null;
  running: boolean;
  /** The chat runs on the agent harness. */
  harness: boolean;
  isAdmin: boolean;
  /** The picker's list for this chat: `/model` offers exactly these. */
  models: AvailableLlmModel[];
  currentModel: ModelOverride | null;
  selectModel: (model: ModelOverride) => void;
  setEffort: (effort: ReasoningEffort) => void;
  setStrategy: (strategy: AgentStrategy) => void;
  modeLabel: string | null;
  openModelPanel: () => void;
  /** Opens the composer's Collections and connectors selector, as its toolbar button does. */
  openKnowledgePanel: () => void;
  /** What the next message searches now, by name; empty: everything the user can read. */
  knowledgeSelection: string[];
  /** The Stop button's own handler. */
  stop: () => void;
  navigate: (href: string) => void;
  isMobile?: boolean;
  /** The file picker `/upload` opens (a test passes its own). */
  pickFiles?: () => Promise<File[]>;
}

/** The longest focus the server keeps (`x.pipeshub.context.compacted.focus`). */
export const COMPACT_FOCUS_MAX = 500;

const label = (model: { modelFriendlyName?: string; modelName: string }): string =>
  model.modelFriendlyName || model.modelName;

/** The picker's model a typed name means: exact name first, then a unique partial match. */
export function findModel(models: AvailableLlmModel[], typed: string): AvailableLlmModel | null {
  const want = typed.trim().toLowerCase();
  if (!want) return null;
  const names = (m: AvailableLlmModel) => [m.modelFriendlyName, m.modelName, m.modelKey].filter(Boolean).map((n) => n.toLowerCase());
  const exact = models.find((m) => names(m).includes(want));
  if (exact) return exact;
  const partial = models.filter((m) => names(m).some((n) => n.includes(want)));
  return partial.length === 1 ? partial[0] : null;
}

function dispatchIfMounted(name: string, payload?: unknown): boolean {
  // A dispatch with no handler is queued for a later mount; a built-in must not fire later by surprise.
  if (!useCommandStore.getState().handlers[name]) return false;
  return useCommandStore.getState().dispatch(name, payload);
}

function dimension(view: ResolvedRunView | null, path: string): unknown {
  return view?.resolved?.effectiveConfig?.dimensions?.[path]?.value ?? null;
}

const text = (value: unknown): string | null =>
  value === null || value === undefined ? null : typeof value === 'string' ? value : JSON.stringify(value);

const APPROVAL_EFFECTS = ['read', 'workspace', 'write', 'execute'] as const;

function approvalsOf(view: ResolvedRunView | null): Record<string, string> | null {
  const out: Record<string, string> = {};
  // An engine run's rules are its profile's; a loop run's are the `approvals` dimension's classes.
  const loop = dimension(view, 'approvals') as { classes?: Record<string, unknown> } | null;
  for (const effect of APPROVAL_EFFECTS) {
    const value = dimension(view, `engineProfile.approvals.${effect}`) ?? loop?.classes?.[effect];
    if (typeof value === 'string') out[effect] = value;
  }
  return Object.keys(out).length > 0 ? out : null;
}

type StatusTimeline = Pick<
  RunTimelineView,
  'status' | 'model' | 'budget' | 'approvals' | 'executor' | 'reasoningEffort' | 'toolsGranted' | 'codingEngine'
>;

/**
 * `/status`'s card from the run's pin and timeline (§2.2); either may be missing on an older run. Without a pin
 * (the control plane only shadows by default) the timeline's pinned spec says the executor, effort and tools.
 */
/** A pin's digest as `/status` names it: enough of the hash to tell two runs' settings apart. */
export function policyVersion(digest: string): string {
  return digest.replace(/^sha256:/, '').slice(0, 12);
}

export function statusFromRun(view: ResolvedRunView | null, timeline: Partial<StatusTimeline> | null): StatusView {
  const resolved = view?.resolved ?? null;
  const tools = resolved
    ? resolved.effectiveSet.items.filter((item) => item.state === 'ready').reduce((n, item) => n + item.tools.length, 0)
    : (timeline?.toolsGranted ?? null);
  const approvals = approvalsOf(view);
  const pinnedEffort = text(dimension(view, 'engineProfile.reasoningEffort'));
  const executor = text(dimension(view, 'engineProfile.engine')) ?? timeline?.executor ?? (resolved ? 'loop' : null);
  return {
    model: resolved?.llm?.model ?? timeline?.model?.model ?? null,
    effort: pinnedEffort ?? timeline?.reasoningEffort ?? (timeline?.executor !== undefined ? STATUS_EFFORT_DEFAULT : null),
    executor,
    codingEngine: executor === 'loop' && timeline?.codingEngine !== undefined ? (timeline.codingEngine ?? ENGINE_NONE) : null,
    tools,
    approvals: approvals ? Object.entries(approvals).map(([k, v]) => `${k} ${v}`).join(' · ') : null,
    approvalsAsked: approvals === null && Array.isArray(timeline?.approvals) ? timeline.approvals.length : null,
    runStatus: timeline?.status ?? null,
    tokens: timeline?.budget?.committed.tokens ?? null,
    policyVersion: resolved ? policyVersion(resolved.digest) : null,
    legacy: false,
    mode: null,
  };
}

/**
 * `/undo`'s first step, also the `/diff` row's "Undo last turn": the files the last writing turn changed, shown
 * for a confirm. Nothing is restored until it is confirmed (`confirmUndo`).
 */
export async function startUndo(
  session: WorkspaceSession,
  row: { rowKey: string; afterPairs: number; typed: string },
  t: TFunction,
): Promise<void> {
  const results = useBuiltinResults.getState();
  const id = results.add(row.rowKey, { typed: row.typed, afterPairs: row.afterPairs, result: { kind: 'loading' } });
  let result: BuiltinResult;
  try {
    const diff = await ChatApi.getWorkspaceDiff(session, 'turn');
    result = diff.files.length === 0
      ? { kind: 'note', text: t('slashCommands.result.undo.nothing') }
      : { kind: 'undoConfirm', diff, session };
  } catch {
    result = { kind: 'failed' };
  }
  useBuiltinResults.getState().update(row.rowKey, id, result);
}

/** The refusal's key: the busy writer's message is the restore button's own (O7). */
export function undoRefusalKey(code: string): string {
  if (code.endsWith('WORKSPACE_WRITER_BUSY')) return 'durableRun.versions.busy';
  if (code.endsWith('WORKSPACE_CONFLICT')) return 'slashCommands.result.undo.moved';
  return 'durableRun.versions.failed';
}

/**
 * Restores the revision before the last turn, on top of the head the confirm showed (`restoreWorkspaceVersion`,
 * the Workspace versions button's call). Files only (O3): the conversation is never touched. The row it leaves
 * is the saved one, keyed by the revision the restore wrote, so reading it again on reload replaces it.
 */
export async function confirmUndo(rowKey: string, rowId: string): Promise<void> {
  const row = useBuiltinResults.getState().rows[rowKey]?.find((r) => r.id === rowId);
  if (row?.result.kind !== 'undoConfirm' || row.result.busy) return;
  const { diff, session } = row.result;
  useBuiltinResults.getState().update(rowKey, rowId, { ...row.result, busy: true, refused: undefined });
  const answer = await ChatApi.restoreWorkspaceVersion(session, diff.from, diff.head);
  if ('refused' in answer) {
    useBuiltinResults.getState().update(rowKey, rowId, { ...row.result, busy: false, refused: undoRefusalKey(answer.refused) });
    return;
  }
  useWorkspaceHeads.getState().set(session.sessionId, answer.revision);
  useBuiltinResults.getState().replace(rowKey, rowId, {
    id: savedUndoRowId(answer.revision),
    typed: row.typed,
    afterPairs: row.afterPairs,
    result: { kind: 'undone', restoredFrom: diff.from, revision: answer.revision, files: diff.files.length + diff.omitted },
  });
}

/** One saved `/undo` row per `rollback` revision. */
export const savedUndoRowId = (revision: number): string => `undo-revision-${String(revision)}`;

function chatTitle(convId: string): string | undefined {
  const store = useChatStore.getState();
  return [...store.conversations, ...store.sharedConversations, ...store.agentConversations].find((c) => c.id === convId)?.title;
}

async function settle<T>(work: Promise<T>): Promise<T | null> {
  try {
    return await work;
  } catch {
    return null;
  }
}

/**
 * Runs one built-in. `args` is the text after the name; `as` is the name the person typed (an alias such as
 * `/env` is echoed as typed). Greyed-out commands never get here.
 */
export async function runBuiltin(command: BuiltinCommand, args: string, deps: BuiltinDeps, as: string = command.name): Promise<void> {
  const { t, rowKey, afterPairs } = deps;
  const typed = args ? `/${as} ${args}` : `/${as}`;
  const results = useBuiltinResults.getState();
  const row = (result: BuiltinResult) => results.add(rowKey, { typed, afterPairs, result });
  const later = async (work: () => Promise<BuiltinResult>) => {
    const id = row({ kind: 'loading' });
    let result: BuiltinResult;
    try {
      result = await work();
    } catch {
      result = { kind: 'failed' };
    }
    useBuiltinResults.getState().update(rowKey, id, result);
  };
  const convId = deps.slot?.convId ?? null;
  const session = convId ? { ...(deps.agentId ? { agentId: deps.agentId } : {}), sessionId: convId } : null;
  const run: DurableAgentRunHandle | null = deps.harness ? activityRun(deps.slot) : null;

  switch (command.name) {
    case 'help':
      row({ kind: 'help' });
      return;
    case 'new':
      results.clear(rowKey);
      dispatchIfMounted(BUILTIN_BUS.newChat);
      return;
    case 'resume':
      dispatchIfMounted(BUILTIN_BUS.openSearch, args);
      return;
    case 'rename': {
      const title = args.trim();
      if (!convId || !title) return;
      try {
        await ChatApi.renameConversation(convId, title);
        useChatStore.getState().renameConversation(convId, title);
        row({ kind: 'note', text: t('slashCommands.result.renamed', { title }) });
      } catch {
        row({ kind: 'failed' });
      }
      return;
    }
    case 'export': {
      if (!convId) return;
      try {
        await toast.promise(
          exportConversation({
            conversationId: convId,
            title: chatTitle(convId) || t('chat.export.untitled'),
            agentId: deps.agentId,
            labels: { you: t('common.you'), assistant: t('chat.export.assistant'), sources: t('chat.sources') },
          }),
          { loading: t('chat.export.exporting'), success: t('chat.export.done'), error: t('chat.export.failed') },
        );
      } catch {
        // The toast said it failed.
      }
      return;
    }
    case 'copy':
      dispatchIfMounted(BUILTIN_BUS.copyLastAnswer);
      return;
    case 'feedback':
      dispatchIfMounted(BUILTIN_BUS.feedbackLastAnswer, /^(good|like|up|\+)/i.test(args.trim()) ? 'like' : 'dislike');
      return;
    case 'fork':
      dispatchIfMounted(BUILTIN_BUS.continuePrivately);
      return;
    case 'model': {
      if (!args.trim()) {
        deps.openModelPanel();
        return;
      }
      const model = findModel(deps.models, args);
      if (!model) {
        row({ kind: 'note', text: t('slashCommands.result.noSuchModel', { name: args.trim() }) });
        deps.openModelPanel();
        return;
      }
      deps.selectModel({
        modelKey: model.modelKey,
        modelName: model.modelName,
        modelFriendlyName: label(model),
        modelProvider: model.provider,
      });
      row({ kind: 'model', model: label(model), was: deps.currentModel ? label(deps.currentModel) : null, running: deps.running });
      return;
    }
    case 'effort': {
      const value = args.trim().toLowerCase();
      const option = REASONING_EFFORT_OPTIONS.find((o) => o.value === value);
      if (!value) {
        deps.openModelPanel();
        return;
      }
      if (!option) {
        row({ kind: 'note', text: t('slashCommands.result.noSuchEffort', { values: REASONING_EFFORT_OPTIONS.map((o) => o.value).join(', ') }) });
        return;
      }
      deps.setEffort(option.value);
      row({ kind: 'effort', effort: t(option.labelKey, option.defaultLabel), running: deps.running });
      return;
    }
    case 'plan':
      deps.setStrategy('plan-execute');
      row({ kind: 'plan', running: deps.running });
      return;
    case 'stop':
      deps.stop();
      return;
    case 'compact': {
      if (!session) return;
      // Saved on the conversation, never sent: the next run compacts before it reads the next message.
      const focus = args.trim().slice(0, COMPACT_FOCUS_MAX);
      const saved = await ChatApi.requestCompact(session, focus);
      if ('requestedAt' in saved) row({ kind: 'compact', focus });
      else if (saved.refused.endsWith('NOTHING_TO_COMPACT')) row({ kind: 'note', text: t('slashCommands.builtin.compact.reason.nothing') });
      else row({ kind: 'failed' });
      return;
    }
    case 'status':
      if (!run) {
        row({
          kind: 'status',
          run: null,
          status: {
            model: deps.currentModel ? label(deps.currentModel) : null,
            effort: null, executor: null, codingEngine: null, tools: null, approvals: null, approvalsAsked: null, runStatus: null, tokens: null,
            policyVersion: null, legacy: true,
            mode: deps.modeLabel,
          },
        });
        return;
      }
      await later(async () => {
        const [view, timeline] = await Promise.all([settle(ChatApi.getRunResolved(run)), settle(ChatApi.getRunTimeline(run))]);
        if (!view && !timeline) return { kind: 'failed' };
        return { kind: 'status', run, status: statusFromRun(view, timeline) };
      });
      return;
    case 'usage':
      if (!session) return;
      await later(async () => {
        const [chat, timeline] = await Promise.all([
          settle(ChatApi.getConversationUsage(session)),
          run ? settle(ChatApi.getRunTimeline(run)) : Promise.resolve(null),
        ]);
        if (!chat && !timeline) return { kind: 'failed' };
        return {
          kind: 'usage',
          run,
          usage: {
            chat: chat ? { runs: chat.runs, usdMicros: chat.committed.usdMicros, tokens: chat.committed.tokens } : null,
            last: timeline?.budget ?? null,
          },
        };
      });
      return;
    case 'permissions':
      if (!run) return;
      await later(async () => {
        const [authority, view] = await Promise.all([ChatApi.getDecisionAuthority(run), settle(ChatApi.getRunResolved(run))]);
        return { kind: 'permissions', admin: deps.isAdmin, permissions: { ...authority, approvals: approvalsOf(view) } };
      });
      return;
    case 'skills':
      await later(async () => {
        const { SkillsApi } = await import('@/app/(main)/workspace/skills/personal/api');
        const skills = await SkillsApi.listSkills({ status: 'active' });
        return { kind: 'list', list: 'skills', items: skills.map((s) => ({ id: s.name, name: s.name, detail: s.description })) };
      });
      return;
    case 'mcp':
      await later(async () => {
        const { McpServersApi } = await import('@/app/(main)/workspace/mcp-servers/api');
        const { instances } = await McpServersApi.getMyMcpServers(false);
        return {
          kind: 'list',
          list: 'mcp',
          items: instances.map((s) => ({
            id: s._id,
            name: s.name,
            detail: s.isAuthenticated ? t('slashCommands.result.mcpConnected') : t('slashCommands.result.mcpNotConnected'),
          })),
        };
      });
      return;
    case 'agents':
      await later(async () => {
        const { AgentsApi } = await import('@/app/(main)/agents/api');
        const { agents } = await AgentsApi.getAgents({ limit: 20 });
        return { kind: 'list', list: 'agents', items: agents.map((a) => ({ id: a._key, name: a.name, detail: a.description ?? undefined })) };
      });
      return;
    case 'memory':
      deps.navigate('/workspace/memories');
      return;
    case 'diff':
      if (!session) return;
      await later(async () => {
        const diff = await ChatApi.getWorkspaceDiff(session, 'chat');
        useWorkspaceHeads.getState().set(session.sessionId, diff.head);
        return diff.head === 0
          ? { kind: 'note', text: t('slashCommands.reason.noWorkspace') }
          : { kind: 'diff', diff, session };
      });
      return;
    case 'undo':
      if (!session) return;
      await startUndo(session, { rowKey, afterPairs, typed }, t);
      return;
    case 'files':
      if (!session) return;
      await showFiles(session, workspaceArg(args), { rowKey, afterPairs, typed }, t, deps.running);
      return;
    case 'open':
      if (!session || !workspaceArg(args)) return;
      await openFile(session, workspaceArg(args), { rowKey, afterPairs, typed }, t, deps.running);
      return;
    case 'download':
      if (!session) return;
      await download(
        session,
        workspaceArg(args),
        { folder: args.trim().endsWith('/'), title: chatTitle(session.sessionId) || t('chat.export.untitled') },
        { rowKey, afterPairs, typed },
        t,
      );
      return;
    case 'upload': {
      if (!session) return;
      // The picker opens now, in the key press's task: a browser refuses one opened after an await.
      const picked = (deps.pickFiles ?? pickFiles)();
      const folder = workspaceArg(args) || UPLOAD_DEFAULT_FOLDER;
      await uploadFiles(session, await picked, folder, { rowKey, afterPairs, typed });
      return;
    }
    case 'engine':
      // No chat yet (CMDUI2): the choice is held for the first message, which creates the chat with it. A first
      // message already on its way has made its choice.
      if (!session && (deps.slot?.messages.length ?? 0) > 0) return;
      await chooseEngine(session, args, useChatStore.getState().agentEngines, deps.running, { rowKey, afterPairs, typed }, deps.agentId);
      return;
    case 'repo':
      // The picker (WP-26): a new chat holds the choice for its first message, a saved one saves it at once.
      if (!session && (deps.slot?.messages.length ?? 0) > 0) return;
      useRepositoryStore.getState().openPicker();
      return;
    case 'run':
      if (!session) return;
      await runCommand(session, args, nextTurnFields(deps.agentId, deps.currentModel), rowKey, { rowKey, afterPairs, typed }, t);
      return;
    case 'sandbox':
      if (!session) return;
      await showSandbox(session, run, { rowKey, afterPairs, typed }, async (last) => {
        const [view, timeline] = await Promise.all([settle(ChatApi.getRunResolved(last)), settle(ChatApi.getRunTimeline(last))]);
        return statusFromRun(view, timeline).executor;
      });
      return;
    case 'logs':
      openLogs(run, deps.isMobile === true, { rowKey, afterPairs, typed }, t);
      return;
    case 'knowledge':
      // The selector narrows within what the user can read; it never widens access.
      deps.openKnowledgePanel();
      row({
        kind: 'note',
        text: deps.knowledgeSelection.length > 0
          ? t('slashCommands.result.knowledgeNext', { names: deps.knowledgeSelection.join(', ') })
          : t('slashCommands.result.knowledgeAll'),
      });
      return;
    default:
      return;
  }
}
