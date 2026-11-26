/**
 * Conversation grants (UX1, FLOWGRANT; ADR-GRANT-01). "Allow for this conversation" allows one tool to one
 * destination with data at most as sensitive as the run had read when the person allowed it. Node is the grant's
 * only writer: it copies the grant from the worker's own offer (`interaction.explained`), never from the client,
 * and a grant reaches every run of the conversation that acts for the person who gave it, never another person's
 * runs and never another conversation. A person's decline holds the same way (ADR-GRANT-01 D9): one rule for both,
 * keyed by the person, the tool and the destination (or, for an ask that names none, the call's arguments). Pure:
 * the services read the journal and apply these rules.
 */
import { createHash } from 'node:crypto';
import { EXTERNAL_POLICY_RULE } from './approval-pattern';
import type { JournalEvent, JournalEventInput } from './journal';
import { nodeRunEvent } from './execution';

export type GrantSensitivity = 'public' | 'private' | 'unknown';

/** What one "allow for this conversation" allows. */
export interface ConversationGrant {
  toolId: string;
  /** The destination's key as the worker names it (`destination.key`, a digest); never its label. */
  key: string;
  /** The data the run had read when the person allowed it. */
  sensitivity: GrantSensitivity;
  /** Who allowed it (a principal reference), on the grants Node holds; never on what a worker offers. */
  by?: string;
}

/**
 * What one person's decline of a held call covers for their runs in the conversation (ADR-GRANT-01 D9): the same
 * tool to the same destination, or with the same arguments when the ask names no destination, at any sensitivity.
 */
export interface ConversationDecline {
  toolId: string;
  /** The ask's `destination.key`, else its `argsDigest`. */
  key: string;
  /** Who declined: always the actor of the run they declined on. */
  by: string;
  /** Why, as they wrote it; told to every run the decline answers. */
  reason?: string;
}

/** The call an ask holds, as a decline names it: its tool and its destination or arguments. */
export type DeclinableAsk = Pick<ConversationDecline, 'toolId' | 'key'>;

/** The refusal a person gets for "Allow for this conversation" on a run that acts for someone else (D9). */
export const CONVERSATION_GRANT_ACTOR_ONLY_MESSAGE =
  'Only the person this task works for can allow it for the whole conversation. Allow it once or deny it.';

/** The most characters of a decline's reason a copy carries (`interaction.declined.reason`). */
const MAX_DECLINE_REASON_CHARS = 1000;

/**
 * ADR-GRANT-01 D9 (SEC-B-01, SEC-C-04): whether a decision `by` someone reaches a run. A conversation decision is
 * one person's consent, so it answers only runs that act for that person; each participant of a shared
 * conversation decides for their own runs. A grant from before grants named their giver (`by` absent) is the run's
 * own actor's, where it was always read.
 */
export const decisionReaches = (
  principal: string | undefined,
  by: string | undefined,
): boolean => principal === undefined || by === undefined || by === principal;

/**
 * How far a grant reaches: one given while the run had read nothing private never covers private data it has read
 * since. Content nobody can name raises no grant: the classification levels hold it at their highest level, which
 * no grant answers (OWD-15), and a write's own reply is such content. The worker ranks them the same way
 * (`harness_core.effects.flow._GRANT_RANK`).
 */
const GRANT_RANK: Readonly<Record<GrantSensitivity, number>> = {
  public: 0,
  private: 1,
  unknown: 0,
};

const isSensitivity = (value: unknown): value is GrantSensitivity =>
  value === 'public' || value === 'private' || value === 'unknown';

const str = (value: unknown): string | undefined =>
  typeof value === 'string' && value.length > 0 ? value : undefined;

/** Whether a worker's reason offers "allow for this conversation"; never for the org's own webhook ask (G-40). */
export const grantOffered = (
  data: Readonly<Record<string, unknown>>,
): boolean =>
  data['conversationGrant'] === true && data['rule'] !== EXTERNAL_POLICY_RULE;

/**
 * The grant a worker's reason offers, or null when it offers none: no grant was offered, the worker named no
 * destination, or the ask is the org's own policy webhook's (G-40), which a grant never answers.
 */
export function offeredGrant(
  data: Readonly<Record<string, unknown>>,
): ConversationGrant | null {
  if (!grantOffered(data)) return null;
  const destination = data['destination'];
  const toolId = str(data['toolId']);
  const key =
    typeof destination === 'object' && destination !== null
      ? str((destination as Record<string, unknown>)['key'])
      : undefined;
  if (toolId === undefined || key === undefined) return null;
  // A reason that does not say what the run had read is weighed as the most a grant reaches (private; unknown ranks
  // with public), so an offer from before FLOWGRANT holds as it did.
  const sensitivity = isSensitivity(data['sensitivity'])
    ? data['sensitivity']
    : 'private';
  return { toolId, key, sensitivity };
}

/**
 * The call a worker's reason names for a decline (ADR-GRANT-01 D9): its tool and destination, else its arguments
 * digest; null when it names neither (a worker from before `argsDigest`), so the decline stays with that one ask.
 */
export function declinableAsk(
  data: Readonly<Record<string, unknown>>,
): DeclinableAsk | null {
  const toolId = str(data['toolId']);
  const destination = data['destination'];
  const key =
    (typeof destination === 'object' && destination !== null
      ? str((destination as Record<string, unknown>)['key'])
      : undefined) ?? str(data['argsDigest']);
  return toolId !== undefined && key !== undefined ? { toolId, key } : null;
}

/** The first of `declines` that answers an ask for `asked`: the same tool and destination (or arguments). */
export const coveringDecline = (
  declines: readonly ConversationDecline[],
  asked: DeclinableAsk,
): ConversationDecline | undefined =>
  declines.find(
    (decline) => decline.toolId === asked.toolId && decline.key === asked.key,
  );

/** The node `interaction.declined` a copy or the declining run carries, or null when it names no person or call. */
export function namedDecline(
  data: Readonly<Record<string, unknown>>,
): ConversationDecline | null {
  const toolId = str(data['toolId']);
  const key = str(data['key']);
  const by = str(data['by']);
  if (toolId === undefined || key === undefined || by === undefined)
    return null;
  const reason = str(data['reason']);
  return { toolId, key, by, ...(reason !== undefined ? { reason } : {}) };
}

export const DECLINED_EVENT = 'interaction.declined';

/**
 * The `interaction.declined` event: on the run where the person declined it names the ask it answered; a copy on
 * another of their open runs also names that run (`fromRunId`), so no ask there pairs with it by id.
 */
export function declinedEvent(
  interactionId: string,
  decline: ConversationDecline,
  fromRunId?: string,
): JournalEventInput {
  const reason = decline.reason?.trim().slice(0, MAX_DECLINE_REASON_CHARS);
  return {
    ...nodeRunEvent(DECLINED_EVENT, {
      interactionId,
      scope: 'conversation',
      toolId: decline.toolId,
      key: decline.key,
      by: decline.by,
      ...(reason !== undefined && reason.length > 0 ? { reason } : {}),
      ...(fromRunId !== undefined ? { fromRunId } : {}),
    }),
    critical: false,
  };
}

/** Whether `grant` answers an ask that offers `asked`: the same tool and destination, the same or less sensitive data. */
export const grantCovers = (
  grant: ConversationGrant,
  asked: ConversationGrant,
): boolean =>
  grant.toolId === asked.toolId &&
  grant.key === asked.key &&
  GRANT_RANK[asked.sensitivity] <= GRANT_RANK[grant.sensitivity];

/** The first of `grants` that answers an ask offering `asked` (`grantCovers`), or undefined. */
export const coveringGrant = (
  grants: readonly ConversationGrant[],
  asked: ConversationGrant,
): ConversationGrant | undefined =>
  grants.find((grant) => grantCovers(grant, asked));

/** The grant a node `interaction.granted` names itself, or null for one written before grants did. */
export function namedGrant(
  data: Readonly<Record<string, unknown>>,
): ConversationGrant | null {
  const toolId = str(data['toolId']);
  const key = str(data['key']);
  const sensitivity = data['sensitivity'];
  if (toolId === undefined || key === undefined || !isSensitivity(sensitivity))
    return null;
  const by = str(data['by']);
  return { toolId, key, sensitivity, ...(by !== undefined ? { by } : {}) };
}

/**
 * The `interaction.granted` event. On the run where the person decided it names the ask it answers; the copy Node
 * writes on another open run of the conversation also names that run (`fromRunId`), so no reader there pairs it
 * with an ask of its own that happens to share the id.
 */
export function grantedEvent(
  interactionId: string,
  grant: ConversationGrant | undefined,
  fromRunId?: string,
): JournalEventInput {
  return {
    ...nodeRunEvent('interaction.granted', {
      interactionId,
      scope: 'conversation',
      ...(grant !== undefined
        ? {
            toolId: grant.toolId,
            key: grant.key,
            sensitivity: grant.sensitivity,
            ...(grant.by !== undefined ? { by: grant.by } : {}),
          }
        : {}),
      ...(fromRunId !== undefined ? { fromRunId } : {}),
    }),
    critical: false,
  };
}

/** ADR-GRANT-01 D8 (UI-B1 G5): the owner took a grant back; from this event on it answers no ask of the run. */
export const GRANT_REVOKED_EVENT = 'interaction.grant_revoked';

/** The `interaction.grant_revoked` event for `grant` (its tool and destination, every sensitivity), by `by`. */
export const grantRevokedEvent = (
  grant: Pick<ConversationGrant, 'toolId' | 'key'>,
  by: string,
): JournalEventInput => ({
  ...nodeRunEvent(GRANT_REVOKED_EVENT, {
    toolId: grant.toolId,
    key: grant.key,
    by,
  }),
  critical: false,
});

/** Whether `event` is a node revocation of the grant for `grant`'s tool and destination. */
const revokes = (
  event: Pick<JournalEvent, 'type' | 'writer' | 'data'>,
  grant: Pick<ConversationGrant, 'toolId' | 'key'>,
): boolean =>
  event.type === GRANT_REVOKED_EVENT &&
  event.writer === 'node' &&
  event.data?.['toolId'] === grant.toolId &&
  event.data['key'] === grant.key;

/** A grant's id in the conversation's list: its tool and destination, digested (never the destination's label). */
export const conversationGrantId = (
  grant: Pick<ConversationGrant, 'toolId' | 'key'>,
): string =>
  `cg_${createHash('sha256').update(`${grant.toolId}\n${grant.key}`).digest('hex').slice(0, 40)}`;

/** Whether a node `interaction.granted` is a copy from another run (FLOWGRANT): it answers no ask of this run. */
export const isSharedGrant = (
  data: Readonly<Record<string, unknown>>,
): boolean => typeof data['fromRunId'] === 'string';

/** What a run's journal says about grants and declines: what it holds, and its open asks either could answer. */
export interface RunGrantState {
  /** Every grant the run's person holds, by tool and destination, with the widest sensitivity it was given for. */
  held: ConversationGrant[];
  /** The run's open asks that offered a grant, each with what it offered. */
  openOffers: Map<string, ConversationGrant>;
  /** The grants people gave on this run's own asks, by the ask. */
  given: Map<string, ConversationGrant>;
  /** Every decline the run's person holds (D9), the latest per tool and key. */
  declined: ConversationDecline[];
  /** The run's open asks that name their call (`declinableAsk`), each with it. */
  openAsks: Map<string, DeclinableAsk>;
  /** The declines the run's person gave on this run's own asks, by the ask. */
  declinedHere: Map<string, ConversationDecline>;
}

const grantKey = (grant: Pick<ConversationGrant, 'toolId' | 'key'>): string =>
  `${grant.toolId}\u0000${grant.key}`;

/** Keeps the widest grant per tool and destination. */
export function widestGrants(
  grants: Iterable<ConversationGrant>,
): ConversationGrant[] {
  const widest = new Map<string, ConversationGrant>();
  for (const grant of grants) {
    const held = widest.get(grantKey(grant));
    if (
      held === undefined ||
      GRANT_RANK[grant.sensitivity] > GRANT_RANK[held.sensitivity]
    ) {
      widest.set(grantKey(grant), grant);
    }
  }
  return [...widest.values()];
}

/**
 * A run's grants, declines and open asks, from its journal (worker offers, Node decisions). With `principal` (the
 * run's actor), only that person's decisions count (D9): a grant or decline another participant gave, or a copy of
 * one, never answers this run. A grant that names no one (from before grants named their giver) is the run's own.
 */
export function runGrantState(
  events: readonly JournalEvent[],
  principal?: string,
): RunGrantState {
  const offers = new Map<string, ConversationGrant>();
  const asks = new Map<string, DeclinableAsk>();
  const open = new Set<string>();
  const given = new Map<string, ConversationGrant>();
  const declinedHere = new Map<string, ConversationDecline>();
  const declines = new Map<string, ConversationDecline>();
  // Who decided each ask: a grant written before grants named it is the decider's.
  const decidedBy = new Map<string, string>();
  let held: ConversationGrant[] = [];
  for (const event of events) {
    const data = event.data ?? {};
    if (event.type === GRANT_REVOKED_EVENT) {
      held = held.filter((grant) => !revokes(event, grant));
      for (const [id, grant] of given) {
        if (revokes(event, grant)) given.delete(id);
      }
      for (const [key, decline] of declines) {
        if (revokes(event, decline)) declines.delete(key);
      }
      for (const [id, decline] of declinedHere) {
        if (revokes(event, decline)) declinedHere.delete(id);
      }
      continue;
    }
    const id = data['interactionId'];
    if (typeof id !== 'string') continue;
    if (event.type === 'interaction.explained' && event.writer === 'worker') {
      const offered = offeredGrant(data);
      if (offered !== null) offers.set(id, offered);
      else offers.delete(id);
      const asked = declinableAsk(data);
      if (asked !== null) asks.set(id, asked);
      else asks.delete(id);
    } else if (event.type === 'interaction.requested') {
      open.add(id);
    } else if (event.type === 'interaction.resolved') {
      open.delete(id);
      const by = str(data['by']);
      if (by !== undefined) decidedBy.set(id, by);
    } else if (
      event.type === 'interaction.granted' &&
      event.writer === 'node' &&
      data['scope'] === 'conversation'
    ) {
      const named = namedGrant(data);
      const offered = isSharedGrant(data) ? undefined : offers.get(id);
      const found = named ?? offered;
      if (found === undefined) continue;
      const by =
        found.by ?? (isSharedGrant(data) ? undefined : decidedBy.get(id));
      if (!decisionReaches(principal, by)) continue;
      const grant = by !== undefined ? { ...found, by } : found;
      // The person's latest decision for a call stands: allowing it again takes back their decline.
      declines.delete(grantKey(grant));
      held.push(grant);
      if (!isSharedGrant(data)) given.set(id, grant);
    } else if (
      event.type === DECLINED_EVENT &&
      event.writer === 'node' &&
      data['scope'] === 'conversation'
    ) {
      const decline = namedDecline(data);
      if (decline === null || !decisionReaches(principal, decline.by)) continue;
      held = held.filter((grant) => grantKey(grant) !== grantKey(decline));
      declines.set(grantKey(decline), decline);
      if (!isSharedGrant(data)) declinedHere.set(id, decline);
    }
  }
  const openOffers = new Map<string, ConversationGrant>();
  const openAsks = new Map<string, DeclinableAsk>();
  for (const id of open) {
    const offered = offers.get(id);
    if (offered !== undefined) openOffers.set(id, offered);
    const asked = asks.get(id);
    if (asked !== undefined) openAsks.set(id, asked);
  }
  return {
    held: widestGrants(held),
    openOffers,
    given,
    declined: [...declines.values()],
    openAsks,
    declinedHere,
  };
}

/**
 * The decisions `principal` gave anywhere in a conversation's root stream (D9), which every root run of the
 * conversation shares: only decisions that name their person and their call count, since an ask's id repeats
 * across the stream's runs. A later root run of the same person is answered by them as its own run's would be.
 */
export function sessionDecisions(
  events: readonly JournalEvent[],
  principal: string,
): Pick<RunGrantState, 'held' | 'declined'> {
  let held: ConversationGrant[] = [];
  const declines = new Map<string, ConversationDecline>();
  for (const event of events) {
    const data = event.data ?? {};
    if (event.writer !== 'node') continue;
    if (event.type === GRANT_REVOKED_EVENT) {
      held = held.filter((grant) => !revokes(event, grant));
      for (const [key, decline] of declines) {
        if (revokes(event, decline)) declines.delete(key);
      }
    } else if (data['scope'] !== 'conversation') {
      continue;
    } else if (event.type === 'interaction.granted') {
      const grant = namedGrant(data);
      if (grant !== null && grant.by === principal) {
        declines.delete(grantKey(grant));
        held.push(grant);
      }
    } else if (event.type === DECLINED_EVENT) {
      const decline = namedDecline(data);
      if (decline !== null && decline.by === principal) {
        held = held.filter((grant) => grantKey(grant) !== grantKey(decline));
        declines.set(grantKey(decline), decline);
      }
    }
  }
  return { held: widestGrants(held), declined: [...declines.values()] };
}

/** Each open ask one of `declines` answers, with the decline that answers it, in id order. */
export function declinedAsks(
  openAsks: ReadonlyMap<string, DeclinableAsk>,
  declines: readonly ConversationDecline[],
): { interactionId: string; decline: ConversationDecline }[] {
  return [...openAsks]
    .flatMap(([interactionId, asked]) => {
      const decline = coveringDecline(declines, asked);
      return decline !== undefined ? [{ interactionId, decline }] : [];
    })
    .sort((a, b) =>
      a.interactionId < b.interactionId
        ? -1
        : a.interactionId > b.interactionId
          ? 1
          : 0,
    );
}

/** The declines a run does not hold yet (the same tool and key): the ones a copy must bring it. */
export const missingDeclines = (
  held: readonly ConversationDecline[],
  declines: readonly ConversationDecline[],
): ConversationDecline[] =>
  declines.filter((decline) => coveringDecline(held, decline) === undefined);

/** The open asks any of `grants` answers. */
export function coveredAsks(
  openOffers: ReadonlyMap<string, ConversationGrant>,
  grants: readonly ConversationGrant[],
): string[] {
  return answeredAsks(openOffers, grants).map(
    ({ interactionId }) => interactionId,
  );
}

/** Each open ask one of `grants` answers, with the grant that answers it, in id order. */
export function answeredAsks(
  openOffers: ReadonlyMap<string, ConversationGrant>,
  grants: readonly ConversationGrant[],
): { interactionId: string; grant: ConversationGrant }[] {
  return [...openOffers]
    .flatMap(([interactionId, asked]) => {
      const grant = coveringGrant(grants, asked);
      return grant !== undefined ? [{ interactionId, grant }] : [];
    })
    .sort((a, b) =>
      a.interactionId < b.interactionId
        ? -1
        : a.interactionId > b.interactionId
          ? 1
          : 0,
    );
}

/** The grants a run does not hold yet (at that sensitivity or wider): the ones a copy must bring it. */
export const missingGrants = (
  held: readonly ConversationGrant[],
  grants: readonly ConversationGrant[],
): ConversationGrant[] =>
  grants.filter((grant) => !held.some((own) => grantCovers(own, grant)));

/** One grant a conversation holds, as its owner's list shows it (UI-B1 G5). */
export interface ListedGrant extends ConversationGrant {
  grantId: string;
  /** D9: a grant, or a call the person declined for their runs (which lists at the widest sensitivity). */
  decision: 'allowed' | 'declined';
  /** When the person allowed it (the first grant's journal time). */
  grantedAt: string;
  /** The destination as the ask named it to the person (`#sales`, `onyx.app`); null when no ask in reach names it. */
  destination: string | null;
}

/**
 * The grants a conversation holds now, from the events of its streams (the root stream, its open runs' and the
 * runs grants were given on), in any order: each by tool and destination, at the widest sensitivity given, named by
 * the ask it answered; one revoked later is gone. A grant given again after a revocation holds again.
 */
export function conversationGrantList(
  events: readonly JournalEvent[],
): ListedGrant[] {
  const ordered = [...events].sort(
    (a, b) =>
      (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0) ||
      (a.streamId < b.streamId ? -1 : a.streamId > b.streamId ? 1 : 0) ||
      a.seq - b.seq,
  );
  const askKey = (runId: string | undefined, id: string): string =>
    `${runId ?? ''}\u0000${id}`;
  const explained = new Map<string, Readonly<Record<string, unknown>>>();
  const decidedBy = new Map<string, string>();
  const listed = new Map<string, ListedGrant>();
  const origins = new Map<string, string>();
  for (const event of ordered) {
    const data = event.data ?? {};
    if (event.type === GRANT_REVOKED_EVENT) {
      for (const [key, grant] of listed) {
        if (revokes(event, grant)) listed.delete(key);
      }
      continue;
    }
    const id = data['interactionId'];
    if (typeof id !== 'string') continue;
    if (
      event.type === DECLINED_EVENT &&
      event.writer === 'node' &&
      data['scope'] === 'conversation'
    ) {
      const decline = namedDecline(data);
      if (decline === null) continue;
      const key = `${decline.toolId}\u0000${decline.key}`;
      if (listed.get(key)?.decision === 'declined') continue;
      listed.set(key, {
        toolId: decline.toolId,
        key: decline.key,
        sensitivity: 'private',
        by: decline.by,
        decision: 'declined',
        grantId: conversationGrantId(decline),
        grantedAt: event.ts,
        destination: null,
      });
      origins.set(
        key,
        askKey(
          isSharedGrant(data) ? String(data['fromRunId']) : event.runId,
          id,
        ),
      );
      continue;
    }
    if (event.type === 'interaction.explained' && event.writer === 'worker') {
      explained.set(askKey(event.runId, id), data);
    } else if (event.type === 'interaction.resolved') {
      const by = str(data['by']);
      if (by !== undefined) decidedBy.set(askKey(event.runId, id), by);
    } else if (
      event.type === 'interaction.granted' &&
      event.writer === 'node' &&
      data['scope'] === 'conversation'
    ) {
      const origin = askKey(
        isSharedGrant(data) ? String(data['fromRunId']) : event.runId,
        id,
      );
      const offer = explained.get(origin);
      const grant =
        namedGrant(data) ?? (offer !== undefined ? offeredGrant(offer) : null);
      if (grant === null) continue;
      const key = `${grant.toolId}\u0000${grant.key}`;
      const held = listed.get(key);
      const by = grant.by ?? decidedBy.get(origin);
      if (held === undefined || held.decision === 'declined') {
        listed.set(key, {
          ...grant,
          ...(by !== undefined ? { by } : {}),
          decision: 'allowed',
          grantId: conversationGrantId(grant),
          grantedAt: event.ts,
          destination: null,
        });
        origins.set(key, origin);
      } else if (GRANT_RANK[grant.sensitivity] > GRANT_RANK[held.sensitivity]) {
        held.sensitivity = grant.sensitivity;
      }
    }
  }
  return [...listed.entries()]
    .map(([key, grant]) => {
      const destination = explained.get(origins.get(key) ?? '')?.[
        'destination'
      ];
      const label =
        typeof destination === 'object' && destination !== null
          ? str((destination as Record<string, unknown>)['label'])
          : undefined;
      return { ...grant, destination: label ?? null };
    })
    .sort((a, b) =>
      a.grantedAt < b.grantedAt ? -1 : a.grantedAt > b.grantedAt ? 1 : 0,
    );
}
