/**
 * G-12 connect cards: a call that waits for its run's owner to connect the toolset or MCP server it needs. The wait
 * travels the approval card's path (UXINT-1: the same anchor, order and receipt), as an approval of kind `connect`.
 * Node sends `connect_requested` / `connect_resolved` live, names pending waits on the run's timeline for a reload,
 * and saves each wait as an `approval` part of kind `connect`.
 */
import type { InteractionDecision } from '@/lib/contracts/harness/generated/core/enums';
import type {
  ConnectTarget,
  PendingApproval,
  RunConnectStatus,
  RunConnectView,
  SSEApprovalRequestedEvent,
  SSEApprovalResolvedEvent,
} from './types';

const CONNECT_PREFIX = 'ix.connect.';

const text = (value: unknown): string | undefined =>
  typeof value === 'string' && value.trim() ? value : undefined;

const whole = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : undefined;

/** What a card waits on, or null when the value is not one (a newer or broken frame draws nothing). */
export function connectTargetOf(raw: unknown): ConnectTarget | null {
  if (raw === null || typeof raw !== 'object') return null;
  const { item, itemKind, connectPath, instanceId, reason, since, askedAt } = raw as Record<string, unknown>;
  const name = text(item);
  const path = text(connectPath);
  if (!name || !path || (itemKind !== 'toolset' && itemKind !== 'mcp')) return null;
  // Only a path inside the app: the card links there, never to another host.
  if (!path.startsWith('/') || path.startsWith('//')) return null;
  const instance = text(instanceId);
  // The live frame and the timeline name when it was asked; a saved part names when the sign-in was rejected.
  const when = text(since) ?? text(askedAt);
  return {
    item: name,
    itemKind,
    connectPath: path,
    ...(instance ? { instanceId: instance } : {}),
    ...(reason === 'sign_in_rejected' ? { reason, ...(when ? { since: when } : {}) } : {}),
  };
}

/** The `connect_requested` frame (or a timeline's pending wait) as a card the approval path keeps. */
export function connectRequested(raw: unknown): SSEApprovalRequestedEvent | null {
  if (raw === null || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  const interactionId = text(value['interactionId']);
  const connect = connectTargetOf(value);
  if (!interactionId?.startsWith(CONNECT_PREFIX) || !connect) return null;
  const seq = whole(value['seq']);
  const afterTurns = whole(value['afterTurns']);
  const displayName = text(value['displayName']);
  const stepId = text(value['stepId']);
  return {
    interactionId,
    toolCallId: text(value['toolCallId']) ?? interactionId,
    toolName: text(value['toolName']) ?? '',
    ...(displayName ? { displayName } : {}),
    args: '',
    argsSummary: null,
    kind: 'connect',
    connect,
    ...(seq !== undefined ? { seq } : {}),
    ...(afterTurns !== undefined ? { afterTurns } : {}),
    ...(stepId ? { stepId } : {}),
  };
}

/** How a wait ended, as the approval path's decision: connected is approved, skipped is denied. */
const DECISION_OF: Readonly<Record<RunConnectStatus, InteractionDecision | null>> = {
  pending: null,
  connected: 'approved',
  declined: 'denied',
  expired: 'expired',
  // The run ended first: the approval path reads any other decision as closed.
  closed: 'cancelled',
};

/** The `connect_resolved` frame as the approval path's resolution, or null. */
export function connectResolved(raw: unknown): SSEApprovalResolvedEvent | null {
  if (raw === null || typeof raw !== 'object') return null;
  const value = raw as Record<string, unknown>;
  const interactionId = text(value['interactionId']);
  const decision = DECISION_OF[value['status'] as RunConnectStatus];
  if (!interactionId?.startsWith(CONNECT_PREFIX) || !decision) return null;
  return { interactionId, decision };
}

/** A reload's pending connect waits (the stream will not send their `connect_requested` again). */
export function pendingConnects(
  connects: readonly RunConnectView[] | undefined,
): Array<SSEApprovalRequestedEvent & { status: 'pending' }> {
  return (connects ?? []).flatMap((view) => {
    if (view.status !== 'pending') return [];
    const card = connectRequested(view);
    return card ? [{ ...card, status: 'pending' as const }] : [];
  });
}

/** When the vendor rejected the sign-in, in the viewer's own time: today as a time, otherwise with the date. */
export function rejectedTime(since: string, now: Date = new Date()): string {
  const at = new Date(since);
  if (Number.isNaN(at.getTime())) return since;
  const sameDay = at.toDateString() === now.toDateString();
  return sameDay
    ? at.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
    : at.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export const isConnectCard = (card: Pick<PendingApproval, 'kind'>): boolean => card.kind === 'connect';
