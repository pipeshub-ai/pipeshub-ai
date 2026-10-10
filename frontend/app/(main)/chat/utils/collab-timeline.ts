import type { MessageAuthor } from '../collaboration-types';
import type { RespondingAgent } from '../types';
import type { MessagePair } from '../components/message-area/message-pairs';

export const GROUP_WINDOW_MS = 5 * 60 * 1000;

interface RowBase {
  /** ISO time of the row, when it has one. */
  time?: string;
  /** Set when a day divider is drawn above the row: the row's own time. */
  dayDivider?: string;
}

export interface HumanTimelineRow extends RowBase {
  kind: 'human';
  key: string;
  pair: MessagePair;
  /** False when the row continues the previous row from the same author. */
  showHeader: boolean;
}

export interface ReplyTimelineRow extends RowBase {
  kind: 'reply';
  key: string;
  pair: MessagePair;
  /** Set when other messages were posted between the question and this reply. */
  replyingTo?: MessageAuthor | null;
}

export type TimelineRow = HumanTimelineRow | ReplyTimelineRow;

export interface TimelineGroup {
  key: string;
  /** The pair key the list measures and scrolls by; absent on a question that was split from its reply. */
  refKey: string | null;
  rows: TimelineRow[];
}

const validTime = (iso: string | undefined): number | null => {
  if (!iso) return null;
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? null : ms;
};

const dayKey = (ms: number): string => {
  const d = new Date(ms);
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
};

const sameAuthor = (a: MessageAuthor | null | undefined, b: MessageAuthor | null | undefined): boolean =>
  Boolean(a && b && a.userId === b.userId);

/** The question asked again, as is, right after its answer failed: Try again resends it, and one question row is enough. */
const retriesFailedAnswer = (pair: MessagePair, prev: MessagePair | undefined): boolean =>
  prev?.failed === true &&
  !prev.note &&
  prev.question === pair.question &&
  (pair.author === undefined || prev.author?.userId === pair.author?.userId);

/**
 * Turns message pairs into timeline rows. A pair with an answer is a human row plus a reply row; a note or an
 * unanswered question is a human row. A question that notes were posted after is drawn where it was asked, and its
 * reply says who it replies to.
 */
export function buildTimeline(pairs: readonly MessagePair[]): TimelineGroup[] {
  const groups: TimelineGroup[] = [];
  for (const [index, pair] of pairs.entries()) {
    const human: HumanTimelineRow = { kind: 'human', key: `${pair.key}:human`, pair, showHeader: true, time: pair.createdAt };
    if (pair.unanswered && !pair.note && retriesFailedAnswer(pair, pairs[index - 1])) {
      // Someone else's retry still running: the failed pair above already shows the question.
      continue;
    }
    if (pair.note || pair.unanswered) {
      groups.push({ key: pair.key, refKey: pair.key, rows: [human] });
      continue;
    }
    const reply: ReplyTimelineRow = { kind: 'reply', key: `${pair.key}:reply`, pair, time: pair.answeredAt };
    const between = Math.min(pair.interleavedNotes ?? 0, groups.length);
    if (between > 0) {
      reply.replyingTo = pair.author ?? null;
      groups.splice(groups.length - between, 0, { key: `${pair.key}:question`, refKey: null, rows: [human] });
      groups.push({ key: pair.key, refKey: pair.key, rows: [reply] });
    } else if (retriesFailedAnswer(pair, pairs[index - 1])) {
      groups.push({ key: pair.key, refKey: pair.key, rows: [reply] });
    } else {
      groups.push({ key: pair.key, refKey: pair.key, rows: [human, reply] });
    }
  }

  let prev: TimelineRow | null = null;
  let prevDay: string | null = null;
  for (const group of groups) {
    for (const row of group.rows) {
      const ms = validTime(row.time);
      if (ms !== null) {
        const day = dayKey(ms);
        if (day !== prevDay) row.dayDivider = row.time;
        if (row.kind === 'human' && prev?.kind === 'human' && !row.dayDivider) {
          const prevMs = validTime(prev.time);
          if (prevMs !== null && ms - prevMs >= 0 && ms - prevMs <= GROUP_WINDOW_MS && sameAuthor(prev.pair.author, row.pair.author)) {
            row.showHeader = false;
          }
        }
        prevDay = day;
      }
      prev = row;
    }
  }
  return groups;
}

export type ResponderKind = 'assistant' | 'agent' | 'guest';

export interface Responder {
  kind: ResponderKind;
  /** `null` when the name is not known and the caller supplies a generic one. */
  name: string | null;
}

/** Who wrote a reply: a guest agent named on the row, else the chat's own agent, else the assistant. */
export function resolveResponder(
  respondingAgent: RespondingAgent | undefined,
  threadAgentId: string | null | undefined,
  agentName: string | null | undefined,
): Responder {
  if (respondingAgent) return { kind: 'guest', name: respondingAgent.name?.trim() || null };
  if (threadAgentId?.trim()) return { kind: 'agent', name: agentName?.trim() || null };
  return { kind: 'assistant', name: null };
}
