import { JournalEvent } from '../domain/journal';
import { TimelineBuilder } from './timeline';

/** The kernel names an unknown effect's interaction `ix.unknown.<invocationId>` (fold). */
export const UNKNOWN_EFFECT_PREFIX = 'ix.unknown.';

export type ReconcileVerdict = 'applied' | 'not_applied' | 'still_unknown';

/** An effect whose outcome nobody knows, parked for an operator (P2.9). */
export interface UnknownEffectView {
  interactionId: string;
  invocationId: string;
  toolName: string;
  displayName?: string;
  argsSummary?: string;
  /** What the reconciler's read answered before it parked the effect; absent without a reconciler. */
  reconcile?: { verdict: ReconcileVerdict; reason: string; lookups: number };
  parkedAt: string;
  status: 'open' | 'resolved';
}

/** What the chat's card shows of a parked effect (UX3): the call, never its arguments. */
export interface UnknownEffectCard {
  interactionId: string;
  toolCallId: string | null;
  toolName: string;
  displayName?: string;
  argsSummary: string | null;
  reconcile?: UnknownEffectView['reconcile'];
}

/** The card for `interactionId` from a run's timeline so far, or null when it names no unknown effect. */
export function unknownEffectCard(
  builder: TimelineBuilder,
  interactionId: unknown,
  verdicts: ReadonlyMap<string, NonNullable<UnknownEffectView['reconcile']>>,
): UnknownEffectCard | null {
  if (
    typeof interactionId !== 'string' ||
    !interactionId.startsWith(UNKNOWN_EFFECT_PREFIX) ||
    interactionId.length === UNKNOWN_EFFECT_PREFIX.length
  ) {
    return null;
  }
  const invocationId = interactionId.slice(UNKNOWN_EFFECT_PREFIX.length);
  const call = builder.callForInvocation(invocationId);
  const reconcile = verdicts.get(invocationId);
  return {
    interactionId,
    toolCallId: call?.callId ?? null,
    toolName: call?.name ?? 'tool',
    ...(call?.displayName !== undefined
      ? { displayName: call.displayName }
      : {}),
    argsSummary: call?.argsSummary ?? null,
    ...(reconcile !== undefined ? { reconcile } : {}),
  };
}

/** A `tool.reconciled` event's verdict, keyed by its invocation; null for anything else. */
export function reconcileVerdict(data: Record<string, unknown>): {
  invocationId: string;
  verdict: NonNullable<UnknownEffectView['reconcile']>;
} | null {
  const invocationId = str(data['invocationId']);
  if (invocationId === undefined) return null;
  return {
    invocationId,
    verdict: {
      verdict: data['verdict'] as ReconcileVerdict,
      reason: str(data['reason']) ?? '',
      lookups: Number(data['lookups'] ?? 0),
    },
  };
}

const str = (value: unknown): string | undefined =>
  typeof value === 'string' && value.length > 0 ? value : undefined;

/**
 * A run's unknown effects as its journal tells them: the call (from the
 * timeline's fold), the last reconcile verdict and whether an interaction
 * resolution closed it. Arguments themselves are never part of the view.
 */
export function foldUnknownEffects(
  events: readonly JournalEvent[],
  runId: string,
): UnknownEffectView[] {
  const builder = new TimelineBuilder(runId);
  const verdicts = new Map<string, UnknownEffectView['reconcile']>();
  const views: UnknownEffectView[] = [];
  for (const event of events) {
    builder.apply(event);
    if (event.runId !== runId) continue;
    const data: Record<string, unknown> = event.data ?? {};
    if (event.type === 'tool.reconciled') {
      const found = reconcileVerdict(data);
      if (found !== null) verdicts.set(found.invocationId, found.verdict);
      continue;
    }
    const interactionId = str(data['interactionId']);
    if (!interactionId?.startsWith(UNKNOWN_EFFECT_PREFIX)) continue;
    if (event.type === 'interaction.requested') {
      const invocationId = interactionId.slice(UNKNOWN_EFFECT_PREFIX.length);
      const call = builder.callForInvocation(invocationId);
      const reconcile = verdicts.get(invocationId);
      views.push({
        interactionId,
        invocationId,
        toolName: call?.name ?? 'tool',
        ...(call?.displayName !== undefined
          ? { displayName: call.displayName }
          : {}),
        ...(call?.argsSummary !== undefined
          ? { argsSummary: call.argsSummary }
          : {}),
        ...(reconcile !== undefined ? { reconcile } : {}),
        parkedAt: event.ts,
        status: 'open',
      });
    } else if (event.type === 'interaction.resolved') {
      const view = views.find((v) => v.interactionId === interactionId);
      if (view !== undefined) view.status = 'resolved';
    }
  }
  return views;
}
