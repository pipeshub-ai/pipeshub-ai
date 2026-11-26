import { approvalAskRules } from '@/config';
import type { TFunction } from 'i18next';
import { latestSignIn, type EngineSignInLine } from '@/chat/engine-signin';
import {
  EXTERNAL_POLICY_RULE,
  SITE_UPLOAD_RULE,
  childAttribution,
  grantDestination,
  interactionTitle,
  isClone,
  isSiteUpload,
  interactionWhy,
  isPlanApproval,
} from '@/chat/interaction-wording';
import { groupApprovals, isOpenApproval, type ApprovalGroup } from '@/chat/interactions';
import { mcpSignInServers } from '@/chat/mcp-sign-in';
import type {
  AskUserQuestionPayload,
  McpSignInServer,
  MessagePart,
  PendingApproval,
  PendingAskUserQuestion,
  SSEEngineProgressEvent,
} from '@/chat/types';
import { askQuestion, midSentence, plainAction } from './approval-words';
import { answeredCount, keyedAnswers, questionsOf } from './question-stepper';
import type { NeedsYouAction, NeedsYouAsk, NeedsYouQuestion } from './view-models';
import type { Words } from './words';

/**
 * Every open ask of a chat as the floating "Needs you" card shows it (design `needs-you-card.tsx`, plan "Needs you"):
 * approvals (one card per batch, "Allow all N", "Allow for this chat" where the rule offers it), the run's question
 * (options and free text; typing in the message box answers too), MCP and engine sign-ins, and connect requests.
 *
 * Each ask names the action against the safe alternative ("Allow" / "Don't allow"), with one plain line of why from
 * the interaction's reason fields and the rest under "Why?". An ask that expired is not listed: silence is never a
 * yes, and its trail stamp says it did not go ahead. Wording comes from the classic chat's readers
 * (`interaction-wording.ts`), so both interfaces name a call the same way. Pure: no React, no store.
 */

/** An ask, with what its actions act on. */
export type NeedsYouItem =
  | { ask: NeedsYouAsk; source: 'approval'; group: ApprovalGroup }
  | { ask: NeedsYouAsk; source: 'question'; question: PendingAskUserQuestion }
  | { ask: NeedsYouAsk; source: 'mcp'; servers: McpSignInServer[] }
  | { ask: NeedsYouAsk; source: 'engine'; signIn: EngineSignInLine };

export interface NeedsYouSnapshot {
  isStreaming: boolean;
  pendingApprovals: ReadonlyArray<PendingApproval>;
  pendingAskUserQuestion: PendingAskUserQuestion | null;
  /** The newest reply's id: only its question still waits (a newer turn skipped an older one). */
  newestReplyId: string | null;
  /** The newest reply's saved parts (its sign-in card), when it is not streaming. */
  newestParts: ReadonlyArray<MessagePart>;
  /** The live run's parts: an engine root asks for its sign-in in its progress frames. */
  streamingParts: ReadonlyArray<MessagePart>;
  /** The person may decide (the chat's owner); someone it is shared with sees nothing to press. */
  canDecide: boolean;
  now?: number;
}

const tf = (t: Words) => t as unknown as TFunction;

/** The one plain line of why, by the rule that holds the call. */
export function reasonLine(group: ApprovalGroup, t: Words): string {
  const first = group.items[0];
  const rule = first.reason?.rule;
  if (first.kind === 'unknown_effect') return t('ui.needsYou.reason.unknownEffect');
  if (isPlanApproval(first)) return t('ui.needsYou.reason.plan');
  switch (rule) {
    case 'flow_external_write':
      return t('ui.needsYou.reason.outside');
    case SITE_UPLOAD_RULE:
      return t('ui.needsYou.reason.upload');
    case 'engine_writes':
      return t('ui.needsYou.reason.engine');
    case EXTERNAL_POLICY_RULE:
      return t('ui.needsYou.reason.externalPolicy');
    case 'writes':
    case 'effect_class':
    case 'tool':
      return t('ui.needsYou.reason.policy');
    default: {
      const key = first.reason ? approvalAskRules.lineKey(first.reason) : undefined;
      return t(key ?? 'ui.needsYou.reason.default');
    }
  }
}

/** Under "Why?": the rule's own words, where the call sends things, and what the run read before it. */
function whyLines(group: ApprovalGroup, t: Words): string[] {
  const first = group.items[0];
  const lines: string[] = [];
  const full = interactionWhy(tf(t), first, group.items.length);
  if (full) lines.push(full);
  const sources = (first.reason?.sources ?? []).map((s) => s.title).filter((s): s is string => Boolean(s));
  if (sources.length) {
    const more = first.reason?.moreSources ?? 0;
    lines.push(t('ui.needsYou.why.read', { sources: sources.slice(0, 3).join(', ') }) + (more > 0 ? ` ${t('ui.needsYou.why.more', { count: more })}` : ''));
  }
  return lines;
}

const refusalKey = (code: string | null | undefined): string | undefined => (code ? 'ui.needsYou.refused' : undefined);

function approvalAsk(group: ApprovalGroup, t: Words, canDecide: boolean): NeedsYouAsk {
  const open = group.items.filter(isOpenApproval);
  const first = open[0] ?? group.items[0];
  const count = open.length;
  const sending = open.some((a) => a.status === 'sending');
  const error = refusalKey(open.find((a) => a.errorCode)?.errorCode);
  const base = {
    key: `approval:${first.runId ?? ''}:${first.childRunId ?? ''}:${first.interactionId}`,
    count,
    why: whyLines(group, t),
    ...(first.childRunId || first.childEngine ? { from: childAttribution(tf(t), first) } : {}),
    ...(first.childRunId ? { jobRunId: first.childRunId } : {}),
    ...(sending ? { sending: true } : {}),
    ...(error ? { error: t(error) } : {}),
  };
  if (first.kind === 'connect' && first.connect) {
    const item = first.connect.item;
    return {
      ...base,
      kind: 'connect',
      title: t('ui.needsYou.connect.title', { item }),
      reason: t(first.connect.reason === 'sign_in_rejected' ? 'ui.needsYou.connect.rejected' : 'ui.needsYou.connect.reason', { item, action: plainAction(t, first) }),
      href: first.connect.connectPath,
      actions: canDecide
        ? [
            { id: 'connect', label: t('ui.needsYou.connect.connect', { item }), tone: 'primary' },
            { id: 'connected', label: t('ui.needsYou.connect.done'), tone: 'secondary' },
            { id: 'skip', label: t('ui.needsYou.connect.skip'), tone: 'quiet' },
          ]
        : [],
    };
  }
  if (first.kind === 'unknown_effect') {
    return {
      ...base,
      kind: 'approval',
      title: t('ui.needsYou.unknownEffect.title', { action: midSentence(plainAction(t, first)) }),
      reason: reasonLine(group, t),
      actions: canDecide
        ? [
            { id: 'happened', label: t('ui.needsYou.unknownEffect.happened'), tone: 'primary' },
            { id: 'deny', label: t('ui.needsYou.unknownEffect.didNot'), tone: 'secondary' },
          ]
        : [],
    };
  }
  const actions: NeedsYouAction[] = [
    { id: 'approve', label: count > 1 ? t('ui.needsYou.allowAll', { count }) : t('ui.needsYou.allow'), tone: 'primary' },
    { id: 'deny', label: count > 1 ? t('ui.needsYou.dontAllowAll') : t('ui.needsYou.dontAllow'), tone: 'secondary' },
  ];
  if (first.reason?.conversationGrant && open.every((a) => a.reason?.conversationGrant)) {
    const destination = grantDestination(open);
    actions.push({ id: 'conversation', label: destination ? t('ui.needsYou.allowChatFor', { destination }) : t('ui.needsYou.allowChat'), tone: 'quiet' });
  }
  // Site uploads, clones and pushes name their place in the title already; every other card says the action.
  const special = isSiteUpload(first) || isClone(first) || /(?:^|[/_])repository_push$/.test(first.toolName);
  const title = special ? interactionTitle(tf(t), first, count) : askQuestion(t, first, count);
  const destination = grantDestination(open);
  const addsPlace = destination && !title.toLowerCase().includes(destination.toLowerCase());
  return {
    ...base,
    kind: 'approval',
    title,
    ...(addsPlace ? { detail: t('ui.needsYou.sendsTo', { destination }) } : {}),
    reason: reasonLine(group, t),
    actions: canDecide ? actions : [],
  };
}

const expired = (payload: AskUserQuestionPayload, now: number): boolean => {
  const at = payload.expiresAt ? Date.parse(payload.expiresAt) : NaN;
  return Number.isFinite(at) && at <= now;
};

function questionAsk(question: PendingAskUserQuestion, t: Words, canDecide: boolean): NeedsYouAsk {
  // Normalized as the classic card reads it, so a merged ask's earlier answers match their questions.
  const items = questionsOf(question.payload);
  const questions: NeedsYouQuestion[] = items.map((q) => ({
    id: q.uuid,
    text: q.question,
    multi: q.multiSelect,
    options: q.options.map((o) => ({ id: o.id, label: o.label, ...(o.description ? { detail: o.description } : {}), ...(o.isUserInput ? { freeText: true } : {}) })),
  }));
  return {
    key: `question:${question.assistantMessageId}`,
    kind: 'question',
    title: questions.length === 1 ? questions[0].text : t('ui.needsYou.question.title', { count: questions.length }),
    reason: t('ui.needsYou.question.reason'),
    why: question.payload.userIntent ? [question.payload.userIntent] : [],
    count: questions.length,
    questions,
    progress: { answered: answeredCount(items, keyedAnswers(items, question.answers)), total: items.length },
    ...(question.status === 'submitted' ? { sending: true } : {}),
    actions: canDecide ? [{ id: 'submit', label: t('ui.needsYou.question.send'), tone: 'primary' }] : [],
  };
}

function engineFrames(parts: ReadonlyArray<MessagePart>): SSEEngineProgressEvent[] {
  return parts.flatMap((p) => (p.type === 'engine_task' ? ((p.frames ?? []) as SSEEngineProgressEvent[]) : []));
}

/** The chat's open asks, oldest first. The first is the card's; the rest wait their turn. */
export function needsYou(snapshot: NeedsYouSnapshot, t: Words): NeedsYouItem[] {
  const items: NeedsYouItem[] = [];
  // Asks live as long as their run: once it ended, an open card is closed by the run's own resolution frames.
  const approvals = snapshot.isStreaming ? snapshot.pendingApprovals : [];
  for (const group of groupApprovals(approvals.filter(isOpenApproval))) {
    items.push({ ask: approvalAsk(group, t, snapshot.canDecide), source: 'approval', group });
  }
  const q = snapshot.pendingAskUserQuestion;
  // A submitted answer shows as "sending" while the run it resumed works; once that run ended the answer is a record
  // (the trail's stamp), not an ask. Nothing moves a submitted card on, so without this it waited forever.
  if (q && (q.status === 'pending' || (q.status === 'submitted' && snapshot.isStreaming)) && q.assistantMessageId === snapshot.newestReplyId && !expired(q.payload, snapshot.now ?? Date.now())) {
    items.push({ ask: questionAsk(q, t, snapshot.canDecide), source: 'question', question: q });
  }
  if (snapshot.isStreaming) {
    const signIn = latestSignIn(engineFrames(snapshot.streamingParts));
    if (signIn?.state === 'requested') {
      items.push({
        ask: {
          key: `engine:${signIn.attemptId}`,
          kind: 'signin',
          title: t('ui.needsYou.engine.title'),
          reason: t('ui.needsYou.engine.reason'),
          why: [],
          count: 1,
          actions: snapshot.canDecide ? [{ id: 'signin', label: t('ui.needsYou.engine.signIn'), tone: 'primary' }] : [],
        },
        source: 'engine',
        signIn,
      });
    }
  } else {
    const servers = mcpSignInServers(snapshot.newestParts as MessagePart[]);
    if (servers.length) {
      items.push({
        ask: {
          key: `mcp:${servers.map((s) => s.instanceId).join(',')}`,
          kind: 'signin',
          title: t('ui.needsYou.mcp.title', { servers: servers.map((s) => s.serverName).join(', ') }),
          reason: t('ui.needsYou.mcp.reason'),
          why: [],
          count: servers.length,
          actions: snapshot.canDecide
            ? [
                { id: 'signin', label: t('ui.needsYou.mcp.signIn'), tone: 'primary' },
                { id: 'retry', label: t('ui.needsYou.mcp.retry'), tone: 'secondary' },
              ]
            : [],
        },
        source: 'mcp',
        servers,
      });
    }
  }
  return items;
}

/** A job's own sign-in ask (its engine's progress frames, read from its stream), as the same card. */
export function jobSignInItems(jobs: ReadonlyArray<{ runId: string; frames: ReadonlyArray<SSEEngineProgressEvent>; live: boolean }>, t: Words, canDecide: boolean): NeedsYouItem[] {
  const items: NeedsYouItem[] = [];
  for (const job of jobs) {
    if (!job.live) continue;
    const signIn = latestSignIn([...job.frames]);
    if (signIn?.state !== 'requested') continue;
    items.push({
      ask: {
        key: `engine:${job.runId}:${signIn.attemptId}`,
        kind: 'signin',
        title: t('ui.needsYou.engine.title'),
        reason: t('ui.needsYou.engine.reason'),
        why: [],
        count: 1,
        jobRunId: job.runId,
        actions: canDecide ? [{ id: 'signin', label: t('ui.needsYou.engine.signIn'), tone: 'primary' }] : [],
      },
      source: 'engine',
      signIn,
    });
  }
  return items;
}
