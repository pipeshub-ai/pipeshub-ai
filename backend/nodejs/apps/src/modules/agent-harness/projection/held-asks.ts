import {
  coveringDecline,
  coveringGrant,
  DECLINED_EVENT,
  GRANT_REVOKED_EVENT,
  runGrantState,
} from '../domain/conversation-grants';
import type { JournalEvent } from '../domain/journal';

/**
 * How long a live stream waits for Node to answer an ask a held grant covers before it draws the card anyway. Node
 * answers in the commit's own request (`answerHeld`, two commits back to back), so this only bounds a failed or lost
 * answer, after which the person decides the ask.
 */
export const HELD_ASK_GRACE_MS = 3_000;

const GRANT_EVENTS = new Set([
  'interaction.explained',
  'interaction.requested',
  'interaction.resolved',
  'interaction.granted',
  DECLINED_EVENT,
  GRANT_REVOKED_EVENT,
]);

/**
 * WP115-FU-2 (ADR-GRANT-01 D7, D9): the asks of one run that a conversation grant or a person's decline it already
 * held when they were raised covers. Node answers each at once as the grant's person (`ConversationGrantReach.answerHeld`, on the same journal
 * fold, `runGrantState` and `coveringGrant`), so a live stream holds the card and draws it with its answer, already
 * decided, as a reload shows it; never an open ask that turns into a receipt a moment later.
 */
export class HeldAsks {
  private readonly events: JournalEvent[] = [];
  private readonly held = new Map<string, number>();

  constructor(private readonly now: () => number = Date.now) {}

  /** Every event of the run, in seq order; only the grant fold's are kept. */
  apply(event: JournalEvent): void {
    if (GRANT_EVENTS.has(event.type)) this.events.push(event);
  }

  /**
   * Whether the ask just raised (`apply`d) is held: a grant the run holds covers it and it was raised within the
   * grace, so Node's answer is still to come. False: draw it now.
   */
  hold(interactionId: string, raisedAt: string): boolean {
    const until = Date.parse(raisedAt) + HELD_ASK_GRACE_MS;
    if (!(until > this.now())) return false;
    const state = runGrantState(this.events);
    const offer = state.openOffers.get(interactionId);
    const asked = state.openAsks.get(interactionId);
    const covered =
      (offer !== undefined && coveringGrant(state.held, offer) !== undefined) ||
      (asked !== undefined &&
        coveringDecline(state.declined, asked) !== undefined);
    if (!covered) return false;
    this.held.set(interactionId, until);
    return true;
  }

  /** True once for a held ask: its answer (or the grace's end) is when its card is drawn. */
  release(interactionId: string): boolean {
    return this.held.delete(interactionId);
  }

  /** The held asks whose grace ended by `now`, released. */
  due(now: number): string[] {
    const due = [...this.held]
      .filter(([, until]) => until <= now)
      .map(([id]) => id);
    for (const id of due) this.held.delete(id);
    return due;
  }

  get count(): number {
    return this.held.size;
  }

  /** Whether the ask is held now: its card is not drawn yet. */
  holds(interactionId: string): boolean {
    return this.held.has(interactionId);
  }

  /** When the next held ask's grace ends; null when none is held. */
  until(): number | null {
    let next: number | null = null;
    for (const until of this.held.values()) {
      if (next === null || until < next) next = until;
    }
    return next;
  }
}
