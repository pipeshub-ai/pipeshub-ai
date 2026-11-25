import { conversationLink } from '../domain/run-end-notice';
import {
  INBOX_SUBJECT_VERSION,
  subjectText,
  type InboxItem,
  type InboxSubject,
} from '../domain/inbox';
import type { InboxViewer, InboxWaitingSource } from '../ports/inbox';
import type {
  ApprovalQueueService,
  PendingApprovalItem,
} from './approval-queue';
import type { InboxQuestion, QuestionInboxService } from './question-inbox';
import type { MemoryService, MemoryViewer } from './memory-service';
import type { MemoryView } from '../domain/memory';

const MAX_DESCRIPTION = 280;

/** A contract timestamp (epoch ms, or ISO text) as epoch ms; undefined when it is neither. */
const timeOf = (value: string | number | undefined): number | undefined => {
  if (typeof value === 'number') return value;
  const parsed = value === undefined ? NaN : Date.parse(value);
  return Number.isFinite(parsed) ? parsed : undefined;
};
const clip = (text: string): string =>
  Array.from(text).length <= MAX_DESCRIPTION
    ? text
    : `${Array.from(text)
        .slice(0, MAX_DESCRIPTION - 1)
        .join('')}…`;

/** The approvals waiting on the viewer that they may decide (the approvals queue's rule), one item each. */
export class ApprovalWaitingSource implements InboxWaitingSource {
  readonly name = 'approvals' as const;
  constructor(private readonly queue: Pick<ApprovalQueueService, 'pending'>) {}

  async waiting(
    viewer: InboxViewer,
    limit: number,
    purpose: 'feed' | 'count' = 'feed',
  ): Promise<InboxItem[]> {
    const items = await this.queue.pending({
      orgId: viewer.orgId,
      actorId: viewer.userId,
      limit,
      audited: purpose === 'feed',
    });
    return items
      .filter((item) => item.authority.approve || item.authority.deny)
      .map(approvalItem);
  }
}

/** The app a tool id names: its toolset or MCP server (`jira__create_issue`, `mcp.github.list_prs`); null for none. */
export function toolApp(toolName: string): string | null {
  const parts = toolName.split(/__|\./).filter((part) => part.length > 0);
  if (parts.length < 2) return null;
  const [first, second] = parts;
  if (first === undefined) return null;
  const app =
    first === 'mcp' && second !== undefined && parts.length > 2
      ? second
      : first;
  return subjectText(app, 128) ?? null;
}

function approvalSubject(item: PendingApprovalItem): InboxSubject {
  const connect = subjectText(item.connect?.item, 256);
  if (connect !== undefined) return { connect };
  const tool = subjectText(item.toolName, 512);
  const destination = subjectText(item.reason?.destination, 512);
  return {
    action: {
      name:
        subjectText(item.displayName ?? item.toolName, 512) ?? item.toolName,
      app: toolApp(item.toolName),
      // ADR-INBOX-02: the client words the call from its id as the approval card does, so the two never drift.
      ...(tool !== undefined ? { tool } : {}),
      ...(destination !== undefined ? { destination } : {}),
    },
  };
}

export function approvalItem(item: PendingApprovalItem): InboxItem {
  const what = item.displayName ?? item.toolName;
  const connect = item.connect;
  const pointer = {
    type: 'conversation' as const,
    conversationId: item.sessionId,
    ...(item.agentKey !== undefined ? { agentKey: item.agentKey } : {}),
    runId: item.runId,
    interactionId: item.interactionId,
  };
  return {
    id: `a:${item.runId}:${item.interactionId}`,
    kind: 'approval',
    severity: 'warning',
    waiting: true,
    unread: true,
    archived: false,
    title:
      connect !== undefined
        ? `Connect ${connect.item} to continue`
        : `Approve: ${what}`,
    description: clip(
      connect !== undefined
        ? 'A task is waiting for you to connect this before it goes on.'
        : item.argsSummary !== undefined && !item.argsHidden
          ? item.argsSummary
          : 'A task is waiting for your decision before it goes on.',
    ),
    at: timeOf(item.requestedAt) ?? item.runUpdatedAt,
    pointer,
    link: conversationLink(item.sessionId, item.agentKey),
    subjectVersion: INBOX_SUBJECT_VERSION,
    subject: approvalSubject(item),
  };
}

/** The questions the viewer's own runs asked them that nobody answered. */
export class QuestionWaitingSource implements InboxWaitingSource {
  readonly name = 'questions' as const;
  constructor(private readonly inbox: Pick<QuestionInboxService, 'pending'>) {}

  async waiting(viewer: InboxViewer, limit: number): Promise<InboxItem[]> {
    return (
      await this.inbox.pending({
        orgId: viewer.orgId,
        actorId: viewer.userId,
        limit,
      })
    ).map(questionItem);
  }
}

export function questionItem(question: InboxQuestion): InboxItem {
  return {
    id: `q:${question.runId}:${question.callId}`,
    kind: 'question',
    severity: 'info',
    waiting: true,
    unread: true,
    archived: false,
    title: 'A question for you',
    description: clip(question.question),
    at: question.runUpdatedAt,
    pointer: {
      type: 'conversation',
      conversationId: question.sessionId,
      ...(question.agentKey !== undefined
        ? { agentKey: question.agentKey }
        : {}),
      runId: question.runId,
      callId: question.callId,
    },
    link: conversationLink(question.sessionId, question.agentKey),
    subjectVersion: INBOX_SUBJECT_VERSION,
    subject: { question: subjectText(question.question, 300) ?? 'A question' },
  };
}

/**
 * Memories waiting for review: the viewer's own proposals, and for an org admin the agent memories proposed in the
 * org where the memory service lists them (each listed only where its label passes with the viewer's own
 * credential, as the memory routes list them).
 */
export class MemoryReviewSource implements InboxWaitingSource {
  readonly name = 'memories' as const;
  constructor(
    private readonly memories: Pick<MemoryService, 'listOwn'> & {
      listAgent?(viewer: MemoryViewer): Promise<MemoryView[]>;
    },
  ) {}

  async waiting(viewer: InboxViewer, limit: number): Promise<InboxItem[]> {
    const as = {
      orgId: viewer.orgId,
      userId: viewer.userId,
      authorization: viewer.authorization,
    };
    const [own, agent] = await Promise.all([
      this.memories.listOwn(as, ['proposed']),
      viewer.isAdmin && this.memories.listAgent !== undefined
        ? this.memories.listAgent(as)
        : Promise.resolve([]),
    ]);
    return [...own, ...agent.filter((memory) => memory.status === 'proposed')]
      .slice(0, limit)
      .map((memory) => memoryItem(memory, own.includes(memory)));
  }
}

export function memoryItem(memory: MemoryView, own: boolean): InboxItem {
  return {
    id: `m:${memory.memoryId}`,
    kind: 'memory_review',
    severity: 'info',
    waiting: true,
    unread: true,
    archived: false,
    title: own ? 'Keep this memory?' : 'An agent wants to remember this',
    description: clip(memory.content),
    at: memory.createdAt,
    pointer: {
      type: 'memory',
      memoryId: memory.memoryId,
      ...(memory.agentKey !== null ? { agentKey: memory.agentKey } : {}),
    },
    link: '/workspace/memories/',
    subjectVersion: INBOX_SUBJECT_VERSION,
    subject: { memory: subjectText(memory.content, 280) ?? 'A memory' },
  };
}

/** A skill the learning loop proposed, as the skills service lists it. */
export interface SkillProposal {
  candidateId: string;
  name: string;
  description: string;
  createdAt: number;
}

/** The skills service's pending proposals, asked with the caller's own credential. */
export interface SkillProposalReader {
  pending(authorization: string): Promise<SkillProposal[]>;
}

/**
 * Skill proposals wait on whoever may decide them: the person whose run learned one, and an org admin (the skills
 * service's rule, UI-B4). The service answers only with the person's own credential, which a push has none of.
 */
export class SkillProposalSource implements InboxWaitingSource {
  readonly name = 'skills' as const;
  readonly credentialOnly = true;
  constructor(private readonly reader: SkillProposalReader) {}

  async waiting(viewer: InboxViewer, limit: number): Promise<InboxItem[]> {
    if (viewer.authorization === undefined) return [];
    return (await this.reader.pending(viewer.authorization))
      .slice(0, limit)
      .map(skillItem);
  }
}

export function skillItem(proposal: SkillProposal): InboxItem {
  return {
    id: `s:${proposal.candidateId}`,
    kind: 'skill_proposal',
    severity: 'info',
    waiting: true,
    unread: true,
    archived: false,
    title: `New skill proposed: ${proposal.name}`,
    description: clip(proposal.description),
    at: proposal.createdAt,
    pointer: { type: 'skill_candidate', candidateId: proposal.candidateId },
    link: '/workspace/skills/personal/',
    subjectVersion: INBOX_SUBJECT_VERSION,
    subject: { skill: subjectText(proposal.name, 256) ?? proposal.candidateId },
  };
}
