import { performance } from 'perf_hooks';
import {
  answeredAsks,
  type ConversationDecline,
  type ConversationGrant,
  coveredAsks,
  declinedAsks,
  declinedEvent,
  decisionReaches,
  grantedEvent,
  missingDeclines,
  missingGrants,
  widestGrants,
} from '../domain/conversation-grants';
import { HarnessConflictError } from '../domain/errors';
import {
  HarnessRun,
  InteractionChannel,
  journalStreamId,
} from '../domain/execution';
import { principalRefFor } from '../domain/run-spec';
import type { JournalPreparer, JournalStore } from '../ports/journal-store';
import type { ExecutionService } from './execution-service';
import {
  elapsedMs,
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import type {
  InteractionService,
  ResolveInteractions,
} from './interaction-service';
import { createHash } from 'node:crypto';

/**
 * A person allowed one or more asks of a run for the rest of the conversation, or declined them (ADR-GRANT-01 D9);
 * either holds for that person's runs of the conversation.
 */
export interface GrantGiven {
  /** Absent: approved (a grant). */
  decision?: 'approved' | 'denied';
  orgId: string;
  sessionId: string;
  /** The run whose asks the person allowed. */
  runId: string;
  interactionIds: readonly string[];
  /** Principal reference of the person, the `by` of every ask the grant answers. */
  by: string;
  channel: Extract<InteractionChannel, 'web' | 'api'>;
  /** The decision's own command id: each run's answer derives its own from it, so a retry replays. */
  commandId: string;
}

export interface GrantReached {
  /** Runs that were brought the grant or decline. */
  shared: number;
  /** Open asks it answered, across the person's runs of the conversation. */
  answered: number;
}

/**
 * FLOWGRANT (ADR-GRANT-01): a person's conversation decision reaches every run of the conversation that acts for
 * them (D9: a grant, or a decline with its reason; never another participant's runs). Each run journals on its own
 * stream and a worker reads only its own, so Node, which reads them all, takes the decision to each such open run
 * (a copy of `interaction.granted` or `interaction.declined` naming what it covers, who gave it and where) and
 * answers at once every open ask there it covers, through the usual decision path. A run admitted later is answered
 * from the conversation's root stream (`answerHeld`), and a child is pinned its parent's grants. Best effort after
 * the decision has committed: anything it misses is asked again, never allowed.
 */
const digestOf = (text: string): string =>
  createHash('sha256').update(text).digest('hex').slice(0, 16);

export class ConversationGrantReach<Tx> {
  constructor(
    private readonly executions: Pick<
      ExecutionService,
      'grantState' | 'openSessionRuns'
    >,
    private readonly interactions: Pick<InteractionService, 'resolveAll'>,
    private readonly store: Pick<JournalStore<Tx>, 'append'>,
    private readonly journals: JournalPreparer,
    private readonly transaction: <T>(
      work: (tx: Tx) => Promise<T>,
    ) => Promise<T>,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  async reach(given: GrantGiven): Promise<GrantReached> {
    const started = performance.now();
    const reached: GrantReached = { shared: 0, answered: 0 };
    const declining = given.decision === 'denied';
    const decided = await this.executions.grantState(given.orgId, given.runId);
    const pick = <T>(by: ReadonlyMap<string, T>): T[] =>
      given.interactionIds.flatMap((id) => {
        const found = by.get(id);
        return found !== undefined ? [found] : [];
      });
    const grants = declining ? [] : widestGrants(pick(decided.given));
    const declines = declining ? pick(decided.declinedHere) : [];
    const from = given.interactionIds.find((id) =>
      declining ? decided.declinedHere.has(id) : decided.given.has(id),
    );
    if ((grants.length === 0 && declines.length === 0) || from === undefined) {
      return reached;
    }
    for (const run of await this.executions.openSessionRuns(
      given.orgId,
      given.sessionId,
    )) {
      // SEC-B-01, SEC-C-04 (D9): one person's decision never reaches a run that acts for someone else.
      if (!decisionReaches(principalRefFor(run.actorId), given.by)) continue;
      try {
        let state = await this.executions.grantState(run.orgId, run.runId);
        if (run.runId !== given.runId) {
          const missing = missingGrants(state.held, grants);
          const unheld = missingDeclines(state.declined, declines);
          if (missing.length > 0 || unheld.length > 0) {
            await this.share(run, given, from, missing, unheld);
            reached.shared += 1;
            // Read again after the copy is written: an ask committed meanwhile is answered here, and one committed
            // after the copy finds it held (`answerHeld`), so no ask falls between the two (WP115-FU-2).
            state = await this.executions.grantState(run.orgId, run.runId);
          }
        }
        reached.answered += declining
          ? await this.decline(
              run,
              given,
              declinedAsks(state.openAsks, declines),
            )
          : await this.answer(
              run,
              given,
              coveredAsks(state.openOffers, grants),
            );
      } catch (error) {
        this.logger.warn(
          'interaction.grant_reach_failed',
          harnessLogMeta('interaction.grant_reach_failed', {
            orgId: run.orgId,
            sessionId: run.sessionId,
            runId: run.runId,
            fromRunId: given.runId,
            decision: declining ? 'denied' : 'approved',
            errorType: error instanceof Error ? error.name : typeof error,
          }),
        );
      }
    }
    this.logger.info(
      'interaction.grant_reached',
      harnessLogMeta('interaction.grant_reached', {
        orgId: given.orgId,
        sessionId: given.sessionId,
        runId: given.runId,
        decision: declining ? 'denied' : 'approved',
        grants: grants.length,
        declines: declines.length,
        shared: reached.shared,
        answered: reached.answered,
        ms: elapsedMs(started),
      }),
    );
    return reached;
  }

  /**
   * WP115-FU-2, D9: the asks a worker just raised that a decision of the run's person already answers: a decline of
   * the same call is answered as that decline, with its reason, and a conversation grant that covers it as that
   * grant, each through the usual decision path. Reads what the person decided anywhere in the conversation (the
   * run's stream and the conversation's root stream). A grant can reach a run between the worker's last read of its
   * stream and its ask commit; Node, the decisions' authority, answers it here. Throws when it cannot tell: the
   * caller leaves the asks to the person.
   */
  async answerHeld(
    run: HarnessRun,
    interactionIds: readonly string[],
    commandId: string,
  ): Promise<number> {
    if (interactionIds.length === 0) return 0;
    const state = await this.executions.grantState(run.orgId, run.runId, {
      session: true,
    });
    const actor = principalRefFor(run.actorId);
    const raised = <T>(asks: ReadonlyMap<string, T>): Map<string, T> =>
      new Map([...asks].filter(([id]) => interactionIds.includes(id)));
    let answered = 0;
    // A person's decline of the call wins over a grant of theirs that covers it: the safer of their two answers.
    const declined = declinedAsks(raised(state.openAsks), state.declined);
    const byReason = new Map<
      string,
      { decline: ConversationDecline; ids: string[] }
    >();
    for (const { interactionId, decline } of declined) {
      const key = `${decline.by}\u0000${decline.reason ?? ''}`;
      const group = byReason.get(key) ?? { decline, ids: [] };
      group.ids.push(interactionId);
      byReason.set(key, group);
    }
    for (const [key, { decline, ids }] of byReason) {
      answered += await this.resolveDeclined(run, ids, decline, {
        channel: 'system',
        commandId: `${commandId}:declined:${digestOf(key)}`,
      });
    }
    const declinedIds = new Set(declined.map((each) => each.interactionId));
    const offers = new Map(
      [...raised(state.openOffers)].filter(([id]) => !declinedIds.has(id)),
    );
    const byPerson = new Map<string, string[]>();
    for (const { interactionId, grant } of answeredAsks(offers, state.held)) {
      const by = grant.by ?? actor;
      if (by !== actor) continue;
      byPerson.set(by, [...(byPerson.get(by) ?? []), interactionId]);
    }
    for (const [by, ids] of byPerson) {
      answered += await this.resolveGranted(run, ids, {
        by,
        // Node applied the person's earlier decision: no one answered on a channel now.
        channel: 'system',
        commandId: `${commandId}:held:${by}`,
      });
    }
    if (answered > 0) {
      this.logger.info(
        'interaction.grant_held_answered',
        harnessLogMeta('interaction.grant_held_answered', {
          orgId: run.orgId,
          sessionId: run.sessionId,
          runId: run.runId,
          answered,
          declined: declined.length,
        }),
      );
    }
    return answered;
  }

  private async share(
    run: HarnessRun,
    given: GrantGiven,
    from: string,
    grants: readonly ConversationGrant[],
    declines: readonly ConversationDecline[],
  ): Promise<void> {
    const prepared = await this.journals.prepare({
      orgId: run.orgId,
      sessionId: run.sessionId,
      streamId: journalStreamId(run),
      runId: run.runId,
      writer: 'node',
      events: [
        ...grants.map((grant) => grantedEvent(from, grant, given.runId)),
        ...declines.map((decline) => declinedEvent(from, decline, given.runId)),
      ],
    });
    await this.transaction((tx) => this.store.append(prepared, tx));
  }

  /** The covered asks, approved for the conversation in one decision on their run; ones decided meanwhile stand. */
  private async answer(
    run: HarnessRun,
    given: GrantGiven,
    ids: readonly string[],
  ): Promise<number> {
    const open = ids.filter(
      (id) => !(run.runId === given.runId && given.interactionIds.includes(id)),
    );
    return this.resolveGranted(run, open, {
      by: given.by,
      channel: given.channel,
      commandId: `${given.commandId}:grant:${run.runId}`,
    });
  }

  /** The asks the person's declines answer, each declined with its reason; ones decided meanwhile stand. */
  private async decline(
    run: HarnessRun,
    given: GrantGiven,
    asks: readonly { interactionId: string; decline: ConversationDecline }[],
  ): Promise<number> {
    const open = asks.filter(
      ({ interactionId }) =>
        !(
          run.runId === given.runId &&
          given.interactionIds.includes(interactionId)
        ),
    );
    let answered = 0;
    for (const [index, { interactionId, decline }] of open.entries()) {
      answered += await this.resolveDeclined(run, [interactionId], decline, {
        channel: given.channel,
        commandId: `${given.commandId}:decline:${run.runId}:${index}`,
      });
    }
    return answered;
  }

  /** `ids` declined as `decline` says, with its reason, in one decision on their run; ones decided meanwhile stand. */
  private async resolveDeclined(
    run: HarnessRun,
    ids: readonly string[],
    decline: ConversationDecline,
    decision: {
      channel: Extract<InteractionChannel, 'web' | 'api' | 'system'>;
      commandId: string;
    },
  ): Promise<number> {
    return this.resolveOpen(run, ids, {
      decision: 'denied',
      channel: decision.channel,
      by: decline.by,
      ...(decline.reason !== undefined ? { reason: decline.reason } : {}),
      commandId: decision.commandId,
    });
  }

  /** `ids` approved for the conversation in one decision on their run; ones decided meanwhile stand. */
  private async resolveGranted(
    run: HarnessRun,
    ids: readonly string[],
    decision: {
      by: string;
      channel: Extract<InteractionChannel, 'web' | 'api' | 'system'>;
      commandId: string;
    },
  ): Promise<number> {
    return this.resolveOpen(run, ids, {
      decision: 'approved',
      scope: 'conversation',
      ...decision,
    });
  }

  private async resolveOpen(
    run: HarnessRun,
    ids: readonly string[],
    decision: Pick<
      ResolveInteractions,
      'decision' | 'channel' | 'scope' | 'reason' | 'by' | 'commandId'
    >,
  ): Promise<number> {
    if (ids.length === 0) return 0;
    // D9, defence in depth: whoever calls this, a person's decision only ever answers their own run's asks.
    if (!decisionReaches(principalRefFor(run.actorId), decision.by)) return 0;
    try {
      await this.interactions.resolveAll({
        orgId: run.orgId,
        runId: run.runId,
        interactionIds: ids,
        ...decision,
      });
      return ids.length;
    } catch (error) {
      if (
        error instanceof HarnessConflictError &&
        (error.code === 'INTERACTION_NOT_OPEN' ||
          error.code === 'RUN_CANCELLING')
      ) {
        return 0;
      }
      throw error;
    }
  }
}
