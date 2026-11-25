import { performance } from 'perf_hooks';
import {
  conversationGrantList,
  DECLINED_EVENT,
  grantRevokedEvent,
  type ListedGrant,
} from '../domain/conversation-grants';
import { briefTool } from '../domain/job-brief';
import { HarnessNotFoundError } from '../domain/errors';
import { journalStreamId } from '../domain/execution';
import type { JournalAppend, JournalEvent } from '../domain/journal';
import { principalRefFor } from '../domain/run-spec';
import type { SessionExecutionScope } from '../ports/chat-session-repository';
import type { JournalPreparer, JournalStore } from '../ports/journal-store';
import type { MemberProfile, OrgMemberProfiles } from '../ports/org-members';
import type { ExecutionService } from './execution-service';
import { MAX_JOURNAL_PAGE } from './execution-service';
import {
  elapsedMs,
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import type { JournalService } from './journal-service';
import type { SessionRunService } from './session-run-service';

/** One grant as the owner's list shows it (`conversation-grants.schema.json`): never its destination key. */
export interface ConversationGrantView {
  grantId: string;
  toolId: string;
  tool: { app: string; name: string };
  destination: string | null;
  sensitivity: ListedGrant['sensitivity'];
  decision: ListedGrant['decision'];
  grantedBy: string | null;
  grantedByName: string | null;
  grantedAt: string;
}

export interface ConversationGrantRevocation {
  revoked: ConversationGrantView;
  /** Open runs of the conversation told. */
  runs: number;
}

export interface ConversationGrantServiceDeps<Tx> {
  access: Pick<SessionRunService, 'assertOwner'>;
  executions: Pick<ExecutionService, 'openSessionRuns'>;
  journal: Pick<JournalStore<Tx>, 'readStream' | 'readRun' | 'append'>;
  journals: JournalPreparer & Pick<JournalService, 'resolve'>;
  transaction: <T>(work: (tx: Tx) => Promise<T>) => Promise<T>;
  members?: OrgMemberProfiles | null;
  logger?: HarnessLogger;
}

const PRINCIPAL_PREFIX = 'p_';

/**
 * ADR-GRANT-01 D8 (UI-B1 G5): the conversation grants ("Allow for this chat") its owner sees and takes back, and
 * the calls a person declined for their runs (D9), taken back the same way. A grant
 * lives in the conversation's streams (ADR-GRANT-01 D2): the root stream every root run reads, and the stream of
 * each open job. Revoking writes `interaction.grant_revoked` on every open run and, when no root run is open, on the
 * root stream itself, in one transaction: from then on no run of the conversation, open or later, is answered by it
 * (Node's held-grant check and the worker's read before an ask both fold the revocation), and the next such call asks.
 */
export class ConversationGrantService<Tx> {
  private readonly logger: HarnessLogger;

  constructor(private readonly deps: ConversationGrantServiceDeps<Tx>) {
    this.logger = deps.logger ?? silentHarnessLogger;
  }

  async list(scope: SessionExecutionScope): Promise<ConversationGrantView[]> {
    await this.deps.access.assertOwner(scope);
    return this.views(scope.orgId, await this.held(scope));
  }

  async revoke(
    scope: SessionExecutionScope,
    grantId: string,
  ): Promise<ConversationGrantRevocation> {
    const started = performance.now();
    await this.deps.access.assertOwner(scope);
    const grant = (await this.held(scope)).find(
      (held) => held.grantId === grantId,
    );
    if (grant === undefined) throw new HarnessNotFoundError('Grant not found');
    const by = principalRefFor(scope.actorId);
    const open = await this.deps.executions.openSessionRuns(
      scope.orgId,
      scope.sessionId,
    );
    const appends: JournalAppend[] = open.map((run) => ({
      orgId: run.orgId,
      sessionId: run.sessionId,
      streamId: journalStreamId(run),
      runId: run.runId,
      writer: 'node',
      events: [grantRevokedEvent(grant, by)],
    }));
    // A root run admitted later reads the root stream: with no root run open, the session's own event tells it.
    if (!open.some((run) => run.parent === undefined)) {
      appends.push({
        orgId: scope.orgId,
        sessionId: scope.sessionId,
        streamId: scope.sessionId,
        writer: 'node',
        events: [grantRevokedEvent(grant, by)],
      });
    }
    const prepared = await Promise.all(
      appends.map((append) => this.deps.journals.prepare(append)),
    );
    await this.deps.transaction(async (tx) => {
      for (const append of prepared) await this.deps.journal.append(append, tx);
    });
    this.logger.info(
      'interaction.grant_revoked',
      harnessLogMeta('interaction.grant_revoked', {
        orgId: scope.orgId,
        sessionId: scope.sessionId,
        actorId: scope.actorId,
        grantId,
        runs: open.length,
        ms: elapsedMs(started),
      }),
    );
    const [revoked] = await this.views(scope.orgId, [grant]);
    if (revoked === undefined) throw new Error('A revoked grant has no view');
    return { revoked, runs: open.length };
  }

  /** The grants the conversation holds now: its root stream, its open jobs, and the runs those grants were given on. */
  private async held(scope: SessionExecutionScope): Promise<ListedGrant[]> {
    const { orgId, sessionId } = scope;
    const events = await this.readStream(orgId, sessionId);
    const read = new Set<string>();
    for (const run of await this.deps.executions.openSessionRuns(
      orgId,
      sessionId,
    )) {
      if (run.parent === undefined) continue;
      read.add(run.runId);
      events.push(...(await this.readRun(orgId, run.runId)));
    }
    // Where each copy was given: its ask there names the destination as the person saw it.
    const origins = new Set(
      events.flatMap((event) =>
        (event.type === 'interaction.granted' ||
          event.type === DECLINED_EVENT) &&
        typeof event.data?.['fromRunId'] === 'string'
          ? [event.data['fromRunId']]
          : [],
      ),
    );
    for (const runId of origins) {
      if (
        read.has(runId) ||
        events.some(
          (event) => event.runId === runId && event.streamId === sessionId,
        )
      ) {
        continue;
      }
      read.add(runId);
      events.push(
        ...(await this.readRun(orgId, runId)).filter(
          (event) => event.type === 'interaction.explained',
        ),
      );
    }
    return conversationGrantList(events);
  }

  private async readStream(
    orgId: string,
    streamId: string,
  ): Promise<JournalEvent[]> {
    return this.pages((after) =>
      this.deps.journal.readStream(orgId, streamId, after, MAX_JOURNAL_PAGE),
    );
  }

  private async readRun(orgId: string, runId: string): Promise<JournalEvent[]> {
    return this.pages((after) =>
      this.deps.journal.readRun(orgId, runId, after, MAX_JOURNAL_PAGE),
    );
  }

  private async pages(
    read: (after: number) => Promise<JournalEvent[]>,
  ): Promise<JournalEvent[]> {
    const events: JournalEvent[] = [];
    let cursor = 0;
    for (;;) {
      const page = await read(cursor);
      events.push(...page);
      if (page.length < MAX_JOURNAL_PAGE) break;
      cursor = page[page.length - 1]?.seq ?? cursor;
    }
    return this.deps.journals.resolve(events);
  }

  private async views(
    orgId: string,
    grants: readonly ListedGrant[],
  ): Promise<ConversationGrantView[]> {
    const userIds = [
      ...new Set(
        grants.flatMap((grant) =>
          grant.by?.startsWith(PRINCIPAL_PREFIX) === true
            ? [grant.by.slice(PRINCIPAL_PREFIX.length)]
            : [],
        ),
      ),
    ];
    const profiles =
      userIds.length > 0 && this.deps.members
        ? await this.deps.members.profiles(orgId, userIds)
        : new Map<string, MemberProfile>();
    return grants.map((grant) => {
      const userId =
        grant.by?.startsWith(PRINCIPAL_PREFIX) === true
          ? grant.by.slice(PRINCIPAL_PREFIX.length)
          : null;
      const { app, name } = briefTool(grant.toolId);
      return {
        grantId: grant.grantId,
        toolId: grant.toolId,
        tool: { app, name },
        destination: grant.destination,
        sensitivity: grant.sensitivity,
        decision: grant.decision,
        grantedBy: userId,
        grantedByName:
          userId !== null ? (profiles.get(userId)?.name ?? null) : null,
        grantedAt: grant.grantedAt,
      };
    });
  }
}
