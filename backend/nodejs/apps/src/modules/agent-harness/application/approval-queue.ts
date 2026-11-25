import { editionReasonFields } from '../projection/timeline';
import type { PendingApprovals } from '../contracts/generated/ext/pipeshub/api/pending-approvals';
import { HarnessRun } from '../domain/execution';
import { ExecutionStore } from '../ports/execution-store';
import {
  ApprovalReason,
  ApprovalView,
  ConnectView,
} from '../projection/timeline';
import { redactedArgs, redactTokens } from '../domain/token-redaction';
import { approvalPattern, batchExclusion } from '../domain/approval-pattern';
import { isUnknownEffectInteraction } from './unknown-effects';
import { RunTimelineService } from './run-timeline-service';
import {
  readRuns,
  RecordTranscriptRead,
} from '../domain/transcript-read-audit';
import {
  DecisionAuthority,
  OrgStanding,
  SessionInteractionService,
} from './session-interactions';

export const APPROVAL_QUEUE_MAX_LIMIT = 100;
export const APPROVAL_QUEUE_DEFAULT_LIMIT = 50;

export interface PendingApprovalQuery {
  orgId: string;
  actorId: string;
  agentKey?: string;
  /** Only runs the viewer is the actor of. */
  mine?: boolean;
  limit: number;
  /**
   * False: the caller only counts them (the Inbox badge) and nothing of the calls leaves Node, so no read of another
   * person's arguments is recorded. Absent or true: the items are shown, and the read is audited (SEC-LG-02).
   */
  audited?: boolean;
}

/**
 * One approval the viewer may decide, with what the decision route needs to
 * address it (contracts/harness/ext/pipeshub/api/pending-approvals.schema.json).
 */
export type PendingApprovalItem = PendingApprovals['items'][number];

/**
 * The queue serves other people's calls, so a credential the run's owner put
 * in an argument, a destination or a source title is masked here, not only
 * in the card that renders it.
 */
function maskedReason(reason: ApprovalReason): ApprovalReason {
  return {
    ...reason,
    sources: reason.sources.map((source) => ({
      ...source,
      ...(source.title !== undefined
        ? { title: redactTokens(source.title) }
        : {}),
      ...(source.url !== undefined ? { url: redactTokens(source.url) } : {}),
    })),
    ...(reason.destination !== undefined
      ? { destination: redactTokens(reason.destination) }
      : {}),
  };
}

/**
 * Whether the approvals waiting on a viewer are read from runs other than
 * their own (bound by `AGENT_HARNESS_APPROVAL_QUEUE_REACH`). Either way an
 * item is listed only where the decision route's rule lets the viewer decide.
 */
export interface ApprovalQueueReach {
  othersRuns(
    viewer: { orgId: string; actorId: string },
    standing: OrgStanding,
  ): Promise<boolean>;
  /**
   * ADR-INBOX-02 D2: who besides the run's actor has its approvals in their
   * queue, so a change to its asks reaches their open pages. Absent: nobody.
   */
  audience?(
    run: Pick<HarnessRun, 'orgId' | 'actorId' | 'runId'>,
  ): Promise<string[]>;
}

/** The default reach: the viewer's own runs, the approvals only they can give. */
export const ownRunsOnly: ApprovalQueueReach = {
  othersRuns: () => Promise.resolve(false),
};

/**
 * Pending approvals on the runs the reach covers, as far as the viewer may
 * decide them (P2.11e). Who may decide is `SessionInteractionService`'s rule, the
 * one the decision route enforces; an approval the viewer may neither approve
 * nor deny is never listed. Deciding goes through the decision route.
 */
export class ApprovalQueueService {
  constructor(
    private readonly runs: Pick<ExecutionStore, 'pendingApprovalRuns'>,
    private readonly interactions: Pick<
      SessionInteractionService,
      'authorityFor' | 'standing'
    >,
    /** `connects` (WP-64): the run's connect waits, listed to its owner; absent, the queue lists approvals only. */
    private readonly timelines: Pick<RunTimelineService, 'approvals'> &
      Partial<Pick<RunTimelineService, 'connects'>>,
    /** SEC-LG-02: a held call's arguments shown outside its session are audited before they are sent. */
    private readonly recordRead?: RecordTranscriptRead,
    private readonly reach: ApprovalQueueReach = ownRunsOnly,
  ) {}

  async pending(query: PendingApprovalQuery): Promise<PendingApprovalItem[]> {
    const viewer = { orgId: query.orgId, actorId: query.actorId };
    const standing = await this.interactions.standing(viewer);
    // Without a standing the reach counts, a person approves only runs that act for them; a
    // session owner's deny of a collaborator's run stays in that conversation, so the limit is
    // spent on what the viewer can act on.
    const ownOnly =
      query.mine === true || !(await this.reach.othersRuns(viewer, standing));
    const candidates = await this.runs.pendingApprovalRuns(query.orgId, {
      limit: query.limit,
      ...(query.agentKey !== undefined ? { agentKey: query.agentKey } : {}),
      ...(ownOnly ? { actorId: query.actorId } : {}),
    });
    const items = await Promise.all(
      candidates.map((run) => this.forRun(viewer, standing, run)),
    );
    const listed = items.flat();
    if (query.audited !== false) {
      await this.recordRead?.({
        orgId: query.orgId,
        readerId: query.actorId,
        view: 'approvals_queue',
        runs: readRuns(
          query.actorId,
          listed.filter((entry) => entry.outsider).map((entry) => entry.item),
        ),
      });
    }
    return listed.map((entry) => entry.item);
  }

  /**
   * AP-10: one approval as `pending` lists it, for the person an approval link was sent to; null when it is not
   * pending or they may not decide it. Arguments shown outside the session are audited as the queue's are.
   */
  async one(
    viewer: { orgId: string; actorId: string },
    run: HarnessRun,
    interactionId: string,
  ): Promise<PendingApprovalItem | null> {
    const standing = await this.interactions.standing(viewer);
    const entry = (await this.forRun(viewer, standing, run)).find(
      (each) => each.item.interactionId === interactionId,
    );
    if (entry === undefined) return null;
    await this.recordRead?.({
      orgId: viewer.orgId,
      readerId: viewer.actorId,
      view: 'approvals_queue',
      runs: readRuns(viewer.actorId, entry.outsider ? [entry.item] : []),
    });
    return entry.item;
  }

  /**
   * AP-08: the run's pending calls the viewer may approve, by the decision route's rule, for the approval links a
   * notice sends them. Nothing is shown to the viewer here, so no read is audited; the link's page audits its own.
   */
  async decidable(
    viewer: { orgId: string; actorId: string },
    run: HarnessRun,
  ): Promise<PendingApprovalItem[]> {
    const standing = await this.interactions.standing(viewer);
    return (await this.forRun(viewer, standing, run))
      .map((entry) => entry.item)
      .filter((item) => item.authority.approve);
  }

  /** The run's pending approvals the viewer may decide, each with whether its arguments reach an outsider. */
  private async forRun(
    viewer: { orgId: string; actorId: string },
    standing: OrgStanding,
    run: HarnessRun,
  ): Promise<{ item: PendingApprovalItem; outsider: boolean }[]> {
    const open = new Set(run.openInteractions ?? []);
    const connects = await this.ownerConnects(viewer, run, open);
    const approvals = (await this.timelines.approvals(run)).filter(
      (approval) =>
        approval.status === 'pending' && open.has(approval.interactionId),
    );
    if (approvals.length === 0) return connects;
    const explained = new Map<string, unknown>();
    for (const approval of approvals) {
      if (approval.reason !== undefined) {
        explained.set(approval.interactionId, approval.reason);
      }
    }
    const authority = await this.interactions.authorityFor(
      viewer,
      run,
      standing,
      explained,
    );
    return approvals
      .flatMap((approval) => {
        const decide: DecisionAuthority = {
          approve:
            authority.approves?.(approval.interactionId) ?? authority.approve,
          deny: authority.deny,
        };
        if (!decide.approve && !decide.deny) return [];
        const showArgs = decide.approve || authority.participant;
        return [
          {
            item: item(run, approval, decide, showArgs),
            outsider: showArgs && !authority.participant,
          },
        ];
      })
      .concat(connects);
  }

  /**
   * G-12, WP-64: the run's open connect waits, for its owner alone (only they can connect the item; anyone else may
   * only skip it, in the conversation). One row per wait, naming the item and, for a sign-in the vendor rejected,
   * when; no arguments, nothing to batch.
   */
  private async ownerConnects(
    viewer: { orgId: string; actorId: string },
    run: HarnessRun,
    open: ReadonlySet<string>,
  ): Promise<{ item: PendingApprovalItem; outsider: boolean }[]> {
    if (
      this.timelines.connects === undefined ||
      viewer.actorId !== run.actorId
    ) {
      return [];
    }
    const waits = (await this.timelines.connects(run)).filter(
      (view) => view.status === 'pending' && open.has(view.interactionId),
    );
    return waits.map((view) => ({
      item: connectItem(run, view),
      outsider: false,
    }));
  }
}

function connectItem(run: HarnessRun, view: ConnectView): PendingApprovalItem {
  return {
    runId: run.runId,
    sessionId: run.sessionId,
    ...(run.agentKey !== undefined && run.agentKey.length > 0
      ? { agentKey: run.agentKey }
      : {}),
    actorId: run.actorId,
    runStatus: run.status,
    runUpdatedAt: run.updatedAt,
    interactionId: view.interactionId,
    toolName: view.toolName ?? view.item,
    ...(view.displayName !== undefined
      ? { displayName: view.displayName }
      : {}),
    argsHidden: false,
    authority: { approve: true, deny: true },
    connect: {
      item: view.item,
      itemKind: view.itemKind,
      instanceId: view.instanceId,
      ...(view.reason !== undefined ? { reason: view.reason } : {}),
      askedAt: view.askedAt,
    },
  };
}

function item(
  run: HarnessRun,
  approval: ApprovalView,
  authority: DecisionAuthority,
  showArgs: boolean,
): PendingApprovalItem {
  return {
    runId: run.runId,
    sessionId: run.sessionId,
    ...(run.agentKey !== undefined && run.agentKey.length > 0
      ? { agentKey: run.agentKey }
      : {}),
    actorId: run.actorId,
    runStatus: run.status,
    runUpdatedAt: run.updatedAt,
    interactionId: approval.interactionId,
    toolName: approval.toolName,
    ...(approval.displayName !== undefined
      ? { displayName: approval.displayName }
      : {}),
    ...(approval.approvals !== undefined
      ? {
          approvals: approval.approvals,
        }
      : {}),
    // WP-100: the inbox says since when it waits, as the chat does (WP-97).
    ...(approval.requestedAt !== undefined
      ? { requestedAt: approval.requestedAt }
      : {}),
    ...(showArgs
      ? {
          args: redactedArgs(approval.args),
          ...(approval.argsSummary !== undefined
            ? { argsSummary: redactTokens(approval.argsSummary) }
            : {}),
        }
      : {}),
    argsHidden: !showArgs,
    ...(approval.reason !== undefined
      ? {
          reason: showArgs
            ? maskedReason(approval.reason)
            : {
                rule: approval.reason.rule,
                ...(approval.reason.layer !== undefined
                  ? { layer: approval.reason.layer }
                  : {}),
                ...(approval.reason.sensitivity !== undefined
                  ? { sensitivity: approval.reason.sensitivity }
                  : {}),
                // An edition rule's own fields are kept: they say who decides, not what the call carries.
                ...editionReasonFields(approval.reason),
              },
        }
      : {}),
    authority: { approve: authority.approve, deny: authority.deny },
    // G-13: only a call whose arguments the viewer sees can join a batch, so the batch is what they read.
    ...(showArgs &&
    batchExclusion(approval) === null &&
    !isUnknownEffectInteraction(approval.interactionId)
      ? { pattern: approvalPattern(approval) }
      : {}),
  };
}
