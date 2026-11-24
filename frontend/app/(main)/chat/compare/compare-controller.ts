import { useFeatureFlagsStore, selectChatCompareEnabled } from '@/lib/store/feature-flags-store';
import {
  ASSISTANT_CTX,
  getRequestReasoningEffort,
  isModelReasoningCapable,
  useChatStore,
} from '../store';
import { ChatApi } from '../api';
import { isAskUserQuestionResumeQuery, loadHistoricalMessages } from '../runtime';
import { cancelStreamForSlot, streamMessageForSlot } from '../streaming';
import { isToolApprovalResumeQuery } from '../tool-approval';
import { pickModelInfoFromConversationBundle } from '../utils/apply-conversation-model-info';
import { CompareApi } from './compare-api';
import { useCompareStore } from './compare-store';
import { MAX_COMPARE_MODELS, type BranchRequest, type CompareTurn } from './types';
import type { ConversationMessage, ModelInfo, ModelOverride, StreamChatRequest } from '../types';

let nextTurnId = 1;

/** Is Compare usable right now in the assistant composer (flag, catalog, mode)? */
export function isCompareAvailable(): boolean {
  if (!selectChatCompareEnabled(useFeatureFlagsStore.getState())) return false;
  const chat = useChatStore.getState();
  if (chat.settings.queryMode === 'image') return false;
  return (chat.settings.availableModels[ASSISTANT_CTX]?.models.length ?? 0) >= MAX_COMPARE_MODELS;
}

function catalogHas(model: ModelOverride): boolean {
  const models = useChatStore.getState().settings.availableModels[ASSISTANT_CTX]?.models ?? [];
  return models.some((m) => m.modelKey === model.modelKey && m.modelName === model.modelName);
}

/** The picked models if Compare should run for the next send, else null. */
export function getActiveCompareModels(): ModelOverride[] | null {
  const { enabled, models } = useCompareStore.getState();
  if (!enabled || !isCompareAvailable()) return null;
  if (models.length !== MAX_COMPARE_MODELS) return null;
  // A model deleted since it was picked falls back to a normal single-model send.
  return models.every(catalogHas) ? models : null;
}

const statusOf = (e: unknown): number | undefined => {
  const err = e as { statusCode?: number; response?: { status?: number } } | null;
  return err?.statusCode ?? err?.response?.status;
};

function turnOf(slotId: string): CompareTurn | undefined {
  return useCompareStore.getState().turns[slotId];
}

function patchTurn(slotId: string, turnId: number, patch: Partial<CompareTurn>): void {
  useCompareStore.setState((s) => {
    const turn = s.turns[slotId];
    return turn && turn.turnId === turnId ? { turns: { ...s.turns, [slotId]: { ...turn, ...patch } } } : s;
  });
}

function removeTurn(slotId: string): void {
  useCompareStore.setState((s) => {
    if (!s.turns[slotId]) return s;
    const { [slotId]: _gone, ...turns } = s.turns;
    return { turns };
  });
}

function applyModel(request: { modelKey: string; modelName: string; modelFriendlyName: string; modelProvider?: string }, m: ModelOverride): void {
  request.modelKey = m.modelKey;
  request.modelName = m.modelName;
  request.modelFriendlyName = m.modelFriendlyName;
  if (m.modelProvider) request.modelProvider = m.modelProvider;
  else delete request.modelProvider;
}

/** Starting a new chat switches Compare off: it is a per-conversation choice. */
export function onNewChat(): void {
  useCompareStore.getState().setEnabled(false);
}

/**
 * Leaving the chat page for another page switches Compare off. Only a changed path counts: the
 * page stays mounted while a new chat's URL gains its conversation id, and a remount there is not leaving.
 */
export function onLeaveChat(mountedAt: string): void {
  setTimeout(() => {
    if (window.location.pathname !== mountedAt) onNewChat();
  }, 0);
}

// Moving from one open chat to another starts that chat with Compare off. A new chat's first send only
// creates a slot while nothing was open, so it is never a move.
if (typeof window !== 'undefined') {
  useChatStore.subscribe((state, prev) => {
    if (state.activeSlotId && prev.activeSlotId && state.activeSlotId !== prev.activeSlotId) onNewChat();
  });
}

let watching = false;
/** Leaving the conversation, or losing either slot, must not leave a hidden stream running. */
function watchSlots(): void {
  if (watching) return;
  watching = true;
  useChatStore.subscribe((state, prev) => {
    for (const [primary, turn] of Object.entries(useCompareStore.getState().turns)) {
      if (!state.slots[primary] || (turn.branchSlotId && !state.slots[turn.branchSlotId])) {
        void dropCompare(primary, { cancelPrimary: false });
      // A transient null (the page re-resolving the URL) is not a navigation, and the primary keeps
      // running in the background like any chat; only moving to another chat drops the branch.
      } else if (state.activeSlotId && state.activeSlotId !== prev.activeSlotId && state.activeSlotId !== primary) {
        void dropCompare(primary, { cancelPrimary: false });
      }
    }
  });
}

/**
 * Turns a normal send into the primary half of a compared turn: model A and
 * `compareTurn`. With Compare off, or where it is not offered, the request is
 * left exactly as built.
 */
export function applyCompareToRequest(slotId: string, request: StreamChatRequest, displayQuery: string): boolean {
  if (request.agentId || request.chatMode === 'image') return false;
  const models = getActiveCompareModels();
  if (!models) return false;
  const [primary, second] = models;

  const secondEffort = isModelReasoningCapable(ASSISTANT_CTX, second)
    ? getRequestReasoningEffort(ASSISTANT_CTX, second)
    : null;
  applyModel(request, primary);
  const effort = getRequestReasoningEffort(ASSISTANT_CTX, primary);
  if (effort) request.reasoningEffort = effort;
  else delete request.reasoningEffort;
  request.compareTurn = true;

  const { conversationId: _c, projectId: _p, compareTurn: _t, runId: _r, ...rest } = request;
  const template: BranchRequest = { ...rest };
  applyModel(template, second);
  if (secondEffort) template.reasoningEffort = secondEffort;
  else delete template.reasoningEffort;

  watchSlots();
  useCompareStore.setState((s) => ({
    turns: {
      ...s.turns,
      [slotId]: {
        turnId: nextTurnId++,
        models: [primary, second],
        displayQuery,
        template,
        userMessageId: null,
        parentConvId: null,
        branchConvId: null,
        branchSlotId: null,
        phase: 'starting',
        choosing: false,
        chooseError: null,
      },
    },
  }));
  return true;
}

/** The primary stream saved the question: create the branch and start the second model in it. */
export function onPrimaryTurnStarted(
  slotId: string,
  data: { conversationId?: string; userMessageId?: string },
): void {
  const turn = turnOf(slotId);
  if (!turn || turn.userMessageId || !data.userMessageId || !data.conversationId) return;
  patchTurn(slotId, turn.turnId, { userMessageId: data.userMessageId, parentConvId: data.conversationId });
  void startBranch(slotId);
}

/** The primary stream ended; if it never announced its question the server is not comparing this turn. */
export function onPrimarySettled(slotId: string): void {
  if (turnOf(slotId)?.userMessageId === null) removeTurn(slotId);
}

function evictBranchSlot(branchSlotId: string | null, cancel: boolean): void {
  if (!branchSlotId) return;
  if (cancel) cancelStreamForSlot(branchSlotId);
  useChatStore.getState().evictSlot(branchSlotId);
}

async function startBranch(slotId: string): Promise<void> {
  const turn = turnOf(slotId);
  if (!turn?.userMessageId || !turn.parentConvId) return;
  const { turnId, parentConvId, userMessageId } = turn;
  // Detach before evicting: losing a branch slot that is still referenced ends the whole turn.
  patchTurn(slotId, turnId, { phase: 'starting', branchSlotId: null, branchConvId: null });
  evictBranchSlot(turn.branchSlotId, true);

  let branchConvId: string;
  try {
    branchConvId = await CompareApi.createBranch(parentConvId, userMessageId);
  } catch (error) {
    console.error('[compare] Could not create the branch', error);
    patchTurn(slotId, turnId, { phase: 'failed' });
    return;
  }
  if (turnOf(slotId)?.turnId !== turnId) {
    // The turn was dropped while the branch was being made; remove what the server just created.
    void CompareApi.choose(parentConvId, 'primary').catch(() => undefined);
    return;
  }
  const store = useChatStore.getState();
  const branchSlotId = store.createSlot(branchConvId);
  store.updateSlot(branchSlotId, { isInitialized: true, hasLoaded: true, isOwner: true, sharedWith: [] });
  patchTurn(slotId, turnId, { phase: 'ready', branchConvId, branchSlotId });
  sendBranch(slotId);
}

function sendBranch(slotId: string): void {
  const turn = turnOf(slotId);
  if (!turn?.template || !turn.branchSlotId || !turn.branchConvId) return;
  void streamMessageForSlot(turn.branchSlotId, turn.displayQuery, {
    ...turn.template,
    conversationId: turn.branchConvId,
  });
}

/** A new branch for the same question: the old one holds a half-saved failed run. */
export function retryBranch(slotId: string): void {
  const turn = turnOf(slotId);
  if (!turn?.template || turn.choosing) return;
  void startBranch(slotId);
}

interface DropOptions {
  /** Stop the primary run too (leaving the conversation); false when it is about to be replaced. */
  cancelPrimary: boolean;
}

/** Forgets the compare turn locally, stops the branch and asks the server to discard it. */
export async function dropCompare(slotId: string, { cancelPrimary }: DropOptions): Promise<void> {
  const turn = turnOf(slotId);
  if (!turn) return;
  removeTurn(slotId);
  if (cancelPrimary) cancelStreamForSlot(slotId);
  evictBranchSlot(turn.branchSlotId, true);
  if (turn.parentConvId && turn.branchConvId) {
    await CompareApi.choose(turn.parentConvId, 'primary').catch((error) => {
      console.warn('[compare] Could not discard the branch', error);
    });
  }
}

/** Regenerate/edit rebuilds the primary answer, so there is nothing left to compare. */
export function discardCompare(slotId: string): void {
  void dropCompare(slotId, { cancelPrimary: false });
}

/** Sending the next message without choosing keeps the primary and drops the branch first. */
export function settleBeforeSend(slotId: string): Promise<void> | null {
  return !turnOf(slotId) ? null : dropCompare(slotId, { cancelPrimary: false });
}

async function reloadPrimary(slotId: string, convId: string): Promise<void> {
  try {
    const detail = await ChatApi.fetchConversation(convId);
    const store = useChatStore.getState();
    if (store.slots[slotId]?.convId !== convId) return;
    const { messages, unansweredAskUserQuestion } = loadHistoricalMessages(detail.messages);
    const modelInfo = pickModelInfoFromConversationBundle({
      modelInfo: detail.conversation.modelInfo,
      messages: detail.messages,
    });
    store.updateSlot(slotId, {
      messages,
      rawMessages: detail.messages,
      pendingAskUserQuestion: unansweredAskUserQuestion,
      messagePagination: {
        currentPage: detail.pagination.page,
        hasOlderMessages: detail.pagination.hasNextPage,
        isLoadingOlder: false,
      },
      ...(modelInfo ? { conversationModelInfo: modelInfo } : {}),
    });
    // The question may have been the first, and the title follows it.
    if (detail.conversation.title) store.renameConversation(convId, detail.conversation.title);
    if (modelInfo) store.updateConversationModelInfoInLists(convId, modelInfo);
  } catch (error) {
    console.error('[compare] Failed to reload the conversation after choosing', error);
    useChatStore.getState().invalidateConversation(convId);
  }
}

/** Keeps one answer: `primary` (left) or `branch` (right). Resolves true when the compare ended. */
export async function chooseAnswer(slotId: string, which: 'primary' | 'branch'): Promise<boolean> {
  const turn = turnOf(slotId);
  if (!turn || turn.choosing || !turn.parentConvId) return false;
  if (which === 'branch' && !turn.branchConvId) return false;
  const { turnId, parentConvId, branchSlotId } = turn;
  patchTurn(slotId, turnId, { choosing: true, chooseError: null });
  try {
    await CompareApi.choose(parentConvId, which === 'primary' ? 'primary' : turn.branchConvId!);
  } catch (error) {
    // 404: the choice already went through (a retry, or another tab); the parent holds the result.
    if (statusOf(error) !== 404) {
      patchTurn(slotId, turnId, { choosing: false, chooseError: statusOf(error) === 409 ? 'conflict' : 'failed' });
      return false;
    }
    await reloadPrimary(slotId, parentConvId);
    if (turnOf(slotId)?.turnId === turnId) removeTurn(slotId);
    evictBranchSlot(branchSlotId, true);
    return true;
  }
  if (which === 'branch') await reloadPrimary(slotId, parentConvId);
  if (turnOf(slotId)?.turnId === turnId) removeTurn(slotId);
  evictBranchSlot(branchSlotId, true);
  return true;
}

const toOverride = (info: ModelInfo | undefined): ModelOverride | null =>
  info?.modelKey && info.modelName
    ? { modelKey: info.modelKey, modelName: info.modelName, modelFriendlyName: info.modelFriendlyName || info.modelName }
    : null;

const lastModelInfo = (rows: ConversationMessage[]): ModelInfo | undefined =>
  [...rows].reverse().find((m) => m.messageType === 'bot_response' && m.modelInfo)?.modelInfo;

const restoring = new Set<string>();

const IN_PROGRESS = 'Inprogress';
const FOLLOW_INTERVAL_MS = 3000;
/** A little over the server's detached-run cap, after which a run is abandoned. */
const FOLLOW_MAX_MS = 35 * 60 * 1000;

const lastRowIsAnswer = (rows: ConversationMessage[]): boolean => rows[rows.length - 1]?.messageType === 'bot_response';

function fillBranchSlot(branchSlotId: string, detail: Awaited<ReturnType<typeof ChatApi.fetchConversation>>): void {
  const { messages, unansweredAskUserQuestion } = loadHistoricalMessages(detail.messages);
  useChatStore.getState().updateSlot(branchSlotId, {
    messages,
    rawMessages: detail.messages,
    remoteRunInProgress: detail.conversation.status === IN_PROGRESS,
    pendingAskUserQuestion: unansweredAskUserQuestion,
    messagePagination: {
      currentPage: detail.pagination.page,
      hasOlderMessages: detail.pagination.hasNextPage,
      isLoadingOlder: false,
    },
  });
}

/**
 * A reload drops the branch's stream but the server finishes the run. Refetch the branch until it settles
 * (the single-chat equivalent is use-remote-run-poll, which only watches the active slot's status).
 */
function followBranch(slotId: string, turnId: number, branchConvId: string, branchSlotId: string): void {
  const deadline = Date.now() + FOLLOW_MAX_MS;
  const tick = async (): Promise<void> => {
    const live = useChatStore.getState().slots[branchSlotId];
    if (turnOf(slotId)?.turnId !== turnId || !live || live.convId !== branchConvId) return;
    let running = true;
    // A local stream (Retry) owns the slot; check again later.
    if (!live.isStreaming) {
      try {
        const detail = await ChatApi.fetchConversation(branchConvId);
        if (turnOf(slotId)?.turnId !== turnId || useChatStore.getState().slots[branchSlotId]?.isStreaming) return;
        fillBranchSlot(branchSlotId, detail);
        running = detail.conversation.status === IN_PROGRESS;
        if (!running && !lastRowIsAnswer(detail.messages)) patchTurn(slotId, turnId, { phase: 'failed' });
      } catch {
        // Retried until the deadline.
      }
    }
    if (running && Date.now() < deadline) setTimeout(() => void tick(), FOLLOW_INTERVAL_MS);
    else useChatStore.getState().updateSlot(branchSlotId, { remoteRunInProgress: false });
  };
  setTimeout(() => void tick(), FOLLOW_INTERVAL_MS);
}

/**
 * After a reload: when the conversation's last question still has a branch,
 * bring the two-column view back. The branch is loaded by id like any chat.
 */
export async function restoreCompare(slotId: string): Promise<void> {
  const slot = useChatStore.getState().slots[slotId];
  const convId = slot?.convId;
  if (!slot || !convId || slot.isTemp || !slot.isInitialized || slot.isStreaming || turnOf(slotId)) return;
  if (!selectChatCompareEnabled(useFeatureFlagsStore.getState()) || restoring.has(convId)) return;
  restoring.add(convId);
  try {
    const branches = await CompareApi.listBranches(convId);
    if (branches.length === 0) return;
    const primaryRows = useChatStore.getState().slots[slotId]?.rawMessages ?? [];
    const questionOf = (b: { userMessageId: string }) =>
      primaryRows.find((m) => m._id === b.userMessageId && m.messageType === 'user_query');
    // Answers to a question card or an approval continue the compared turn; any other later question moved
    // the chat on, so a branch left behind (a failed discard, another tab) must not show under it.
    const isCurrent = (b: { userMessageId: string }): boolean => {
      const question = questionOf(b);
      if (!question) return false;
      return !primaryRows
        .slice(primaryRows.indexOf(question) + 1)
        .some(
          (m) =>
            m.messageType === 'user_query' &&
            !isAskUserQuestionResumeQuery(m.content) &&
            !isToolApprovalResumeQuery(m.content),
        );
    };
    const branch = branches.find(isCurrent);
    if (!branch) {
      void CompareApi.choose(convId, 'primary').catch(() => undefined);
      return;
    }
    const lastQuestion = questionOf(branch)!;

    const detail = await ChatApi.fetchConversation(branch.branchConversationId);
    const store = useChatStore.getState();
    const now = store.slots[slotId];
    if (!now || now.convId !== convId || turnOf(slotId) || now.isStreaming) return;

    const branchSlotId = store.createSlot(branch.branchConversationId);
    store.updateSlot(branchSlotId, { isInitialized: true, hasLoaded: true, isOwner: true, sharedWith: [] });
    fillBranchSlot(branchSlotId, detail);
    const first = toOverride(lastModelInfo(primaryRows));
    const answered = lastRowIsAnswer(detail.messages);
    // Before it answers, the branch's rows say nothing about the second model; its session does.
    const second = toOverride(answered ? lastModelInfo(detail.messages) : detail.conversation.modelInfo);
    const running = detail.conversation.status === IN_PROGRESS;
    const secondKnown = Boolean(first && second && second.modelKey !== first.modelKey);
    if (!answered && !running && !secondKnown) {
      // Nothing to show or rerun on the right: keep the primary, the chat goes on as a single one.
      void CompareApi.choose(convId, 'primary').catch(() => undefined);
      evictBranchSlot(branchSlotId, false);
      return;
    }
    const turnId = nextTurnId++;
    watchSlots();
    useCompareStore.setState((s) => ({
      turns: {
        ...s.turns,
        [slotId]: {
          turnId,
          models: first && second ? [first, second] : null,
          displayQuery: lastQuestion.content,
          template:
            !answered && !running && second
              ? { query: lastQuestion.content, chatMode: 'agent', filters: { apps: [], kb: [] }, ...second }
              : null,
          userMessageId: branch.userMessageId,
          parentConvId: convId,
          branchConvId: branch.branchConversationId,
          branchSlotId,
          phase: !answered && !running ? 'failed' : 'ready',
          choosing: false,
          chooseError: null,
        },
      },
    }));
    if (running) followBranch(slotId, turnId, branch.branchConversationId, branchSlotId);
  } catch (error) {
    console.warn('[compare] Could not restore the compare view', error);
  } finally {
    restoring.delete(convId);
  }
}
