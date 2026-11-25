import { JournalEvent } from '../domain/journal';
import { AguiCustomName, AguiFrame, AGUI, AGUI_CUSTOM } from './frames';
import { APPROVAL_COUNTED } from '../domain/interactions';
import {
  APPROVAL_REQUESTED_FRAME,
  APPROVAL_RESOLVED_FRAME,
  approvalCard,
  callResultFrames,
  callStartFrames,
  ENGINE_PROGRESS,
  engineProgressFrame,
  NOTICE_FRAME,
  UNKNOWN_EFFECT_FRAME,
  UNKNOWN_EFFECT_RESOLVED_FRAME,
} from './run-projector';
import { HeldCard } from './stream-id';
import {
  MODEL_SWITCHED,
  REPEATING_NOTICES,
  SANDBOX_QUEUED,
  TIME_PLAN,
  TimelineBuilder,
} from './timeline';
import { isSharedGrant } from '../domain/conversation-grants';
import { HeldAsks } from './held-asks';

/**
 * WP-89: a code_task child's notices its parent's answer shows too, said on the parent's stream under the child's
 * run (a projection relay): its time plan (ADR-RUN-01 D4), its engine version retired and continued (D6), that it
 * waited in the queue for a free sandbox and that its model's provider was down and it moved to another (G-48).
 */
const RELAYED_NOTICES: ReadonlySet<string> = new Set([
  TIME_PLAN,
  SANDBOX_QUEUED,
  MODEL_SWITCHED,
  ...REPEATING_NOTICES,
]);
const NOTICE_EVENTS: ReadonlySet<string> = new Set([
  'x.pipeshub.run.notice',
  'run.yielded',
  'model.fallback',
]);
import {
  reconcileVerdict,
  unknownEffectCard,
  UnknownEffectView,
} from './unknown-effects';

export interface ChildRef {
  childRunId: string;
  role: string;
  /** The parent's `child.spawned` seq: how a stream id names the child. */
  spawnSeq: number;
  /** Spawned and not yet ended. */
  live: boolean;
}

/** Reads a child's journal after `afterSeq` (its own stream, P6.1), payloads resolved. */
export type ChildJournalReader = (
  childRunId: string,
  afterSeq: number,
) => Promise<JournalEvent[]>;

/** A frame stating a child's card, and the cards the client holds once it has the frame. */
export interface StateFrame {
  frame: AguiFrame;
  held: HeldCard[];
}

type OpenKind = 'approval' | 'unknown_effect';

interface OpenCard {
  kind: OpenKind;
  /** The child's journal seq of the request: the card's name in a stream id. */
  requestSeq: number;
  /** When the child asked. */
  ts: string;
}

interface ChildState {
  role: string;
  spawnSeq: number;
  live: boolean;
  builder: TimelineBuilder;
  seq: number;
  verdicts: Map<string, NonNullable<UnknownEffectView['reconcile']>>;
  open: Map<string, OpenCard>;
  /** Request seqs of the cards the client holds open: its cursor's, then each one announced. */
  client: Set<number>;
  /** The first read brings the client from what it holds to now; later reads follow as it happens. */
  primed: boolean;
  /** The child's calls this stream showed: started, or ended with their result. */
  calls: Map<string, 'started' | 'ended'>;
  /** Asks a grant the child holds answers: said with their answer, not before (WP115-FU-2). */
  heldAsks: HeldAsks;
}

/**
 * What a parent's owner must decide for its children (UX3, U3 D-3): a deep
 * run's sub-agent asks on its own stream, which the parent's projection never
 * reads, so its card would wait unseen until the run's deadline. This follows
 * each live child's interactions and says them on the parent's stream,
 * attributed to the child. It says the child's steps too, with their results
 * (CTRESULTS): without them a running code task showed nothing of its work.
 *
 * They are state, not positions of the parent's journal, so a subscription is
 * told the difference between the cards it holds (its cursor names them,
 * QA6-F12) and the cards open now: a card it holds is not announced again, one
 * decided or ended meanwhile is closed, one it does not hold is announced. A
 * cursor that names none (a new page, a client that lost its state) is told
 * every open card.
 */
export class ChildInteractions {
  private readonly children = new Map<string, ChildState>();
  /** The cursor's cards until the run's children are known. */
  private claimed: HeldCard[] | null;

  constructor(
    private readonly read: ChildJournalReader,
    private readonly parentRunId: string,
    held: readonly HeldCard[] = [],
    private readonly now: () => number = Date.now,
  ) {
    this.claimed = [...held];
  }

  /** The cards the client holds open now. */
  held(): HeldCard[] {
    if (this.claimed !== null) return [...this.claimed];
    return [...this.children.values()].flatMap((state) =>
      [...state.client].map((requestSeq) => ({
        spawnSeq: state.spawnSeq,
        requestSeq,
      })),
    );
  }

  /**
   * Follows the run's children as they are now. Returns the ones to start and
   * stop watching, the ended ones whose cards the client still holds (read
   * once, `settle`), and the frames closing an ended child's cards.
   */
  track(children: readonly ChildRef[]): {
    added: string[];
    removed: string[];
    settle: string[];
    frames: StateFrame[];
  } {
    const claimed = this.claimed ?? [];
    this.claimed = null;
    const added: string[] = [];
    const settle: string[] = [];
    for (const child of children) {
      if (this.children.has(child.childRunId)) continue;
      const client = new Set(
        claimed
          .filter((card) => card.spawnSeq === child.spawnSeq)
          .map((card) => card.requestSeq),
      );
      if (!child.live && client.size === 0) continue;
      this.children.set(child.childRunId, {
        role: child.role,
        spawnSeq: child.spawnSeq,
        live: child.live,
        builder: new TimelineBuilder(child.childRunId),
        seq: 0,
        verdicts: new Map(),
        open: new Map(),
        client,
        primed: false,
        calls: new Map(),
        heldAsks: new HeldAsks(this.now),
      });
      (child.live ? added : settle).push(child.childRunId);
    }
    const live = new Set(
      children.filter((child) => child.live).map((child) => child.childRunId),
    );
    const removed: string[] = [];
    const frames: StateFrame[] = [];
    for (const [childRunId, state] of this.children) {
      if (!state.live || live.has(childRunId)) continue;
      state.live = false;
      removed.push(childRunId);
      if (state.primed) {
        // One last read: the steps it journaled after the last one this stream followed, then its cards close.
        settle.push(childRunId);
      } else if (state.client.size > 0) {
        // Its journal was never read: one read says what became of the cards the client holds.
        settle.push(childRunId);
      } else {
        this.children.delete(childRunId);
      }
    }
    return { added, removed, settle, frames };
  }

  async pull(childRunId: string): Promise<StateFrame[]> {
    const state = this.children.get(childRunId);
    if (state === undefined) return [];
    const events = await this.read(childRunId, state.seq);
    const frames: StateFrame[] = [];
    const say = (frame: AguiFrame | null): void => {
      if (frame !== null) frames.push({ frame, held: this.held() });
    };
    const steps: AguiFrame[] = [];
    for (const event of events) {
      if (event.seq <= state.seq) continue;
      state.seq = event.seq;
      const known = new Set(state.builder.timeline.notices);
      state.builder.apply(event);
      if (event.runId !== childRunId) continue;
      state.heldAsks.apply(event);
      const data = event.data ?? {};
      if (NOTICE_EVENTS.has(event.type)) {
        for (const notice of state.builder.timeline.notices) {
          if (known.has(notice) || !RELAYED_NOTICES.has(notice.code)) continue;
          steps.push({
            type: AGUI.CUSTOM,
            name: NOTICE_FRAME,
            value: { ...notice },
            runId: childRunId,
          });
        }
      } else if (event.type === ENGINE_PROGRESS) {
        steps.push(engineProgressFrame(data, childRunId, true));
      } else if (event.type === 'tool.reconciled') {
        const found = reconcileVerdict(data);
        if (found !== null)
          state.verdicts.set(found.invocationId, found.verdict);
      } else if (event.type === 'interaction.requested') {
        const interactionId = this.opened(state, data['interactionId'], event);
        if (
          interactionId !== null &&
          state.primed &&
          !this.heldByGrant(state, interactionId)
        ) {
          state.client.add(event.seq);
          say(this.frameFor(childRunId, state, interactionId));
        }
      } else if (
        event.type === 'interaction.granted' &&
        event.writer === 'node' &&
        data['scope'] === 'conversation' &&
        !isSharedGrant(data) &&
        typeof data['interactionId'] === 'string' &&
        state.builder.approval(data['interactionId']) !== undefined
      ) {
        // FLOWGRANT: the child's ask was allowed for the conversation, by the person or by a grant that covers it.
        say(this.heldCard(childRunId, state, data['interactionId']));
        say(
          this.custom(APPROVAL_RESOLVED_FRAME, {
            interactionId: data['interactionId'],
            decision: 'approved',
            scope: 'conversation',
            childRunId,
          }),
        );
      } else if (event.type === APPROVAL_COUNTED && event.writer === 'node') {
        say(this.counted(childRunId, state, data['interactionId']));
      } else if (event.type === 'interaction.resolved') {
        const interactionId = data['interactionId'];
        if (typeof interactionId !== 'string') continue;
        const card = state.open.get(interactionId);
        if (card === undefined) continue;
        say(this.heldCard(childRunId, state, interactionId));
        state.open.delete(interactionId);
        if (state.client.delete(card.requestSeq)) {
          say(
            this.resolved(
              childRunId,
              card.kind,
              interactionId,
              data['decision'],
            ),
          );
        }
      }
    }
    if (!state.primed) {
      state.primed = true;
      const asked = new Set(
        [...state.open.values()].map((card) => card.requestSeq),
      );
      // A cursor naming a card this child never asked holds nothing.
      for (const requestSeq of [...state.client]) {
        if (!asked.has(requestSeq)) state.client.delete(requestSeq);
      }
      if (state.live) {
        for (const [interactionId, card] of state.open) {
          if (state.client.has(card.requestSeq)) continue;
          if (this.heldByGrant(state, interactionId)) continue;
          state.client.add(card.requestSeq);
          say(this.frameFor(childRunId, state, interactionId));
        }
      }
    }
    steps.push(...this.calls(childRunId, state));
    for (const step of steps) say(step);
    if (!state.live) frames.push(...this.expire(childRunId, state));
    return frames;
  }

  /**
   * A loop child's calls as they start and end, nested under it by their run id as legacy's sub-agent frames were;
   * an engine child's steps are its progress lines. The child's own timeline reads the same journal (U3).
   */
  private calls(childRunId: string, state: ChildState): AguiFrame[] {
    const frames: AguiFrame[] = [];
    for (const call of state.builder.timeline.turns.flatMap((t) => t.calls)) {
      if (call.hidden) continue;
      const shown = state.calls.get(call.callId);
      if (shown === undefined) {
        frames.push(...callStartFrames(call, childRunId));
        state.calls.set(call.callId, 'started');
      }
      if (call.outcome !== undefined && shown !== 'ended') {
        frames.push(...callResultFrames(call, childRunId));
        state.calls.set(call.callId, 'ended');
      }
    }
    return frames;
  }

  /** An ended child decides nothing more: the cards the client holds close as expired, and it is forgotten. */
  private expire(childRunId: string, state: ChildState): StateFrame[] {
    const frames: StateFrame[] = [];
    for (const [interactionId, card] of state.open) {
      if (!state.client.delete(card.requestSeq)) continue;
      frames.push({
        frame: this.resolved(childRunId, card.kind, interactionId, 'expired'),
        held: this.held(),
      });
    }
    this.children.delete(childRunId);
    return frames;
  }

  /** Records a card the child asked for; null for an interaction that is no card. */
  private opened(
    state: ChildState,
    interactionId: unknown,
    event: JournalEvent,
  ): string | null {
    if (typeof interactionId !== 'string') return null;
    const kind: OpenKind | null =
      state.builder.approval(interactionId) !== undefined
        ? 'approval'
        : unknownEffectCard(state.builder, interactionId, state.verdicts) !==
            null
          ? 'unknown_effect'
          : null;
    if (kind === null) return null;
    state.open.set(interactionId, {
      kind,
      requestSeq: event.seq,
      ts: event.ts,
    });
    return interactionId;
  }

  /** Whether a grant the child holds answers this approval it just asked (`HeldAsks`). */
  private heldByGrant(state: ChildState, interactionId: string): boolean {
    const card = state.open.get(interactionId);
    return (
      card?.kind === 'approval' && state.heldAsks.hold(interactionId, card.ts)
    );
  }

  /** A held ask's card, said just before its answer so the client never holds it open; null when not held. */
  private heldCard(
    childRunId: string,
    state: ChildState,
    interactionId: unknown,
  ): AguiFrame | null {
    if (typeof interactionId !== 'string') return null;
    const card = state.open.get(interactionId);
    if (card === undefined || !state.heldAsks.release(interactionId)) {
      return null;
    }
    state.client.add(card.requestSeq);
    return this.frameFor(childRunId, state, interactionId);
  }

  /**
   * WP100-FU-5: one more person approved a child's ask that needs several and still waits, on the card the client holds;
   * counts only, never who, as the run's own card (`RunProjector.approvalCounted`).
   */
  private counted(
    childRunId: string,
    state: ChildState,
    interactionId: unknown,
  ): AguiFrame | null {
    if (typeof interactionId !== 'string') return null;
    const card = state.open.get(interactionId);
    const view = state.builder.approval(interactionId);
    if (
      card === undefined ||
      !state.client.has(card.requestSeq) ||
      view?.approvals === undefined ||
      view.status !== 'pending'
    ) {
      return null;
    }
    return this.custom(AGUI_CUSTOM.APPROVAL_COUNTED, {
      interactionId,
      approvals: { ...view.approvals },
      childRunId,
    });
  }

  /** When a held child ask's card is next due (`releaseHeld`); null when none is held. */
  heldUntil(): number | null {
    let next: number | null = null;
    for (const state of this.children.values()) {
      const until = state.heldAsks.until();
      if (until !== null && (next === null || until < next)) next = until;
    }
    return next;
  }

  /** The cards of held child asks not answered within the grace: the person decides them now. */
  releaseHeld(now: number): StateFrame[] {
    const frames: StateFrame[] = [];
    for (const [childRunId, state] of this.children) {
      for (const interactionId of state.heldAsks.due(now)) {
        const card = state.open.get(interactionId);
        if (card === undefined) continue;
        state.client.add(card.requestSeq);
        const frame = this.frameFor(childRunId, state, interactionId);
        if (frame !== null) frames.push({ frame, held: this.held() });
      }
    }
    return frames;
  }

  private frameFor(
    childRunId: string,
    state: ChildState,
    interactionId: string,
  ): AguiFrame | null {
    const engine = state.builder.timeline.engineKind;
    // UXINT-3: the card names the engine ("Codex wants to …"), not the sub-agent role.
    const attribution = {
      childRunId,
      childRole: state.role,
      ...(engine !== undefined ? { childEngine: engine } : {}),
    };
    const approval = state.builder.approval(interactionId);
    if (approval !== undefined) {
      const card = approvalCard(approval);
      const reason = card['reason'] as Record<string, unknown> | undefined;
      // BUGS-B: nor "Don't ask again" (the rule would be saved from a run the card does not belong to).
      const { ruleGrant: _ruleGrant, ...shown } = reason ?? {};
      return this.custom(APPROVAL_REQUESTED_FRAME, {
        ...card,
        // FLOWGRANT: a child's grant reaches the whole conversation, so the worker's offer stands.
        ...(reason !== undefined ? { reason: shown } : {}),
        ...attribution,
      });
    }
    const unknown = unknownEffectCard(
      state.builder,
      interactionId,
      state.verdicts,
    );
    return unknown === null
      ? null
      : this.custom(UNKNOWN_EFFECT_FRAME, { ...unknown, ...attribution });
  }

  private resolved(
    childRunId: string,
    kind: OpenKind,
    interactionId: string,
    decision: unknown,
  ): AguiFrame {
    return this.custom(
      kind === 'approval'
        ? APPROVAL_RESOLVED_FRAME
        : UNKNOWN_EFFECT_RESOLVED_FRAME,
      { interactionId, decision, childRunId },
    );
  }

  private custom(
    name: AguiCustomName,
    value: Record<string, unknown>,
  ): AguiFrame {
    return { type: AGUI.CUSTOM, name, value, runId: this.parentRunId };
  }
}
