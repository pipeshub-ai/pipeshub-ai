import {
  createInboxStore,
  RepositoryInboxNotices,
} from '../../../libs/repositories/agent-harness/inbox';
import {
  ownRunsOnly,
  type ApprovalQueueReach,
  type ApprovalQueueService,
} from '../application/approval-queue';
import type { ExecutionService } from '../application/execution-service';
import { InboxFeedService } from '../application/inbox-feed';
import {
  ApprovalWaitingSource,
  MemoryReviewSource,
  QuestionWaitingSource,
  SkillProposalSource,
} from '../application/inbox-sources';
import type { MemoryService } from '../application/memory-service';
import type { QuestionInboxService } from '../application/question-inbox';
import {
  RunEndNoticeService,
  RunWatchRegistry,
} from '../application/run-end-notices';
import type { ScheduleService } from '../application/schedule-service';
import { RELEASE_TRIGGER_SETTINGS } from '../application/release-run-settings';
import type { TriggerSettings } from '../ports/schedules';
import { ThreadReplyService } from '../application/thread-replies';
import type { ActorRuns, ExecutionStore } from '../ports/execution-store';
import { isWorkingJob } from '../domain/run-lists';
import type { InboxStore, InboxWaitingSource } from '../ports/inbox';

/** Whether the person is an admin of the org; false when nobody can say. */
export type OrgAdminCheck = (orgId: string, userId: string) => Promise<boolean>;

/** Waiting sources an edition adds to the Inbox's feed (`InboxWaitingSource[]`), bound by its binders; unbound: none. */
export const AGENT_HARNESS_INBOX_EXTRA_SOURCES =
  'AgentHarnessInboxExtraSources';
import type { interfaces } from 'inversify';
import { boundOrNull, HarnessBinder } from './context';
import {
  AGENT_HARNESS_APPROVAL_QUEUE,
  AGENT_HARNESS_APPROVAL_QUEUE_REACH,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_INBOX,
  AGENT_HARNESS_INBOX_STORE,
  AGENT_HARNESS_MEMORIES,
  AGENT_HARNESS_ORG_ADMIN_CHECK,
  AGENT_HARNESS_QUESTION_INBOX,
  AGENT_HARNESS_RUN_WATCHERS,
  AGENT_HARNESS_SCHEDULES,
  AGENT_HARNESS_TRIGGER_SETTINGS,
} from './tokens';

/**
 * G2 (ADR-INBOX-01): the Inbox store, the merged feed over every source bound before it, the finished-run
 * notices told of every run end, and the watch registry the stream routes hold leases with. Runs after
 * the binders of the sources it reads (approvals, questions, memories, schedules, execution).
 */
export const bindInbox: HarnessBinder = ({
  container,
  dependencies,
  logger,
}) => {
  if (container.isBound(AGENT_HARNESS_INBOX)) return;
  const inbox = dependencies.inbox ?? {};
  if (!container.isBound(AGENT_HARNESS_INBOX_STORE)) {
    container
      .bind<InboxStore>(AGENT_HARNESS_INBOX_STORE)
      .toConstantValue(inbox.store ?? createInboxStore());
  }
  const store = container.get<InboxStore>(AGENT_HARNESS_INBOX_STORE);
  if (!container.isBound(AGENT_HARNESS_ORG_ADMIN_CHECK)) {
    const approvers = dependencies.approvers;
    container
      .bind<OrgAdminCheck>(AGENT_HARNESS_ORG_ADMIN_CHECK)
      .toConstantValue(async (orgId, userId) =>
        approvers === undefined ? false : approvers.isOrgAdmin(orgId, userId),
      );
  }

  const waiting: InboxWaitingSource[] = [];
  if (container.isBound(AGENT_HARNESS_APPROVAL_QUEUE)) {
    waiting.push(
      new ApprovalWaitingSource(
        container.get<ApprovalQueueService>(AGENT_HARNESS_APPROVAL_QUEUE),
      ),
    );
  }
  if (container.isBound(AGENT_HARNESS_QUESTION_INBOX)) {
    waiting.push(
      new QuestionWaitingSource(
        container.get<QuestionInboxService>(AGENT_HARNESS_QUESTION_INBOX),
      ),
    );
  }
  if (container.isBound(AGENT_HARNESS_MEMORIES)) {
    waiting.push(
      new MemoryReviewSource(
        container.get<MemoryService>(AGENT_HARNESS_MEMORIES),
      ),
    );
  }
  if (inbox.skills !== undefined)
    waiting.push(new SkillProposalSource(inbox.skills));
  waiting.push(
    ...(boundOrNull<readonly InboxWaitingSource[]>(
      container,
      AGENT_HARNESS_INBOX_EXTRA_SOURCES,
    ) ?? []),
  );

  const schedules = (): ScheduleService | null =>
    container.isBound(AGENT_HARNESS_SCHEDULES)
      ? container.get<ScheduleService>(AGENT_HARNESS_SCHEDULES)
      : null;
  const feed = new InboxFeedService({
    notices: inbox.notices ?? new RepositoryInboxNotices(),
    waiting,
    preferences: store,
    // A mute names a schedule the person acts as, or, as an admin, any of the org's: what the schedule routes let them open.
    schedules: async (orgId, userId, _isAdmin, scheduleId) => {
      const service = schedules();
      if (service === null) return false;
      try {
        await service.get({ orgId, userId, via: 'session' }, scheduleId);
        return true;
      } catch {
        return false;
      }
    },
    ...(inbox.publisher !== undefined ? { publisher: inbox.publisher } : {}),
    // CJ14-FU-1: the person's runs that have not ended (indexed by status, bounded by their active runs), as jobs at work.
    workingJobs: async (orgId, userId) => {
      const runs = boundOrNull<Partial<ActorRuns>>(
        container,
        AGENT_HARNESS_EXECUTION_STORE,
      );
      if (typeof runs?.activeRunsOfActor !== 'function') {
        throw new Error('working jobs are not counted by this store');
      }
      return (await runs.activeRunsOfActor(orgId, userId)).filter(isWorkingJob)
        .length;
    },
    isOrgAdmin: container.get<OrgAdminCheck>(AGENT_HARNESS_ORG_ADMIN_CHECK),
  });
  container.bind<InboxFeedService>(AGENT_HARNESS_INBOX).toConstantValue(feed);
  // ADR-INBOX-02: an ask raised or settled by any command reaches the open pages of its actor and of everyone whose
  // queue lists it (D2), as a count, not at a reload.
  if (container.isBound(AGENT_HARNESS_EXECUTION_SERVICE)) {
    const executions = container.get<ExecutionService>(
      AGENT_HARNESS_EXECUTION_SERVICE,
    );
    // Read at each change: an edition binds its reach after the Inbox.
    const reach = (): ApprovalQueueReach =>
      boundOrNull<ApprovalQueueReach>(
        container,
        AGENT_HARNESS_APPROVAL_QUEUE_REACH,
      ) ?? ownRunsOnly;
    // CJ14-FU-1: a job that starts, goes back to the queue or ends moves the person's Jobs count.
    executions.onRunWorkingChanged(
      (run) => void feed.announce(run.orgId, run.actorId),
    );
    executions.onRunAsksChanged((run) => {
      void feed.announce(run.orgId, run.actorId);
      const current = reach();
      if (current.audience === undefined) return;
      void current.audience(run).then(
        (others) => {
          for (const userId of others) void feed.announce(run.orgId, userId);
        },
        // A push is a hint: whoever was not told counts on their next read.
        () => undefined,
      );
    });
  }
  container
    .bind<RunWatchRegistry>(AGENT_HARNESS_RUN_WATCHERS)
    .toConstantValue(new RunWatchRegistry(store));

  if (
    inbox.notifier !== undefined &&
    container.isBound(AGENT_HARNESS_EXECUTION_SERVICE)
  ) {
    const notices = new RunEndNoticeService({
      leases: store,
      claims: store,
      preferences: store,
      notifier: inbox.notifier,
      channels:
        boundOrNull<TriggerSettings>(
          container,
          AGENT_HARNESS_TRIGGER_SETTINGS,
        ) ?? RELEASE_TRIGGER_SETTINGS,
      ...(dependencies.auditNames?.chats !== undefined
        ? { titles: dependencies.auditNames.chats }
        : {}),
      noticed: (orgId, userId) => void feed.announce(orgId, userId),
      ...(logger !== undefined ? { logger } : {}),
    });
    container
      .get<ExecutionService>(AGENT_HARNESS_EXECUTION_SERVICE)
      .onRunEnded((run) => {
        void notices.runEnded(run);
      });
  }

  if (
    dependencies.slackThreads !== undefined &&
    dependencies.conversationReader !== undefined &&
    container.isBound(AGENT_HARNESS_EXECUTION_SERVICE)
  ) {
    const replies = new ThreadReplyService({
      leases: store,
      claims: store,
      conversations: dependencies.conversationReader,
      poster: dependencies.slackThreads,
      ...(logger !== undefined ? { logger } : {}),
    });
    container
      .get<ExecutionService>(AGENT_HARNESS_EXECUTION_SERVICE)
      .onRunEnded((run) => {
        void replies.runEnded(run);
      });
  }
};

/**
 * ADR-INBOX-02 D4: a question left Needs you because its chat moved on (a turn admitted, or a queued message drained,
 * after it). The question is its asking run's actor's, who need not be the sender, so the run is read for them. A
 * push is a hint: a failed read leaves the count to their next read.
 */
export const questionGoneAnnouncer =
  (container: interfaces.Container) =>
  (orgId: string, askingRunId: string): void => {
    const feed = boundOrNull<InboxFeedService>(container, AGENT_HARNESS_INBOX);
    const runs = boundOrNull<ExecutionStore>(
      container,
      AGENT_HARNESS_EXECUTION_STORE,
    );
    if (feed === null || runs === null) return;
    void runs.get(orgId, askingRunId).then(
      (run) => {
        if (run !== null) void feed.announce(orgId, run.actorId);
      },
      () => undefined,
    );
  };
