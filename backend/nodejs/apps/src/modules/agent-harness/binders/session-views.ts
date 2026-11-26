import type { StoreSession } from '../../../libs/repositories/agent-harness/durable-transaction';
import { ChainTranscriptReadAudit } from '../adapters/chain-transcript-read-audit';
import { inDurableTransaction } from '../../../libs/repositories/agent-harness/durable-transaction';
import {
  ApprovalQueueService,
  ownRunsOnly,
  type ApprovalQueueReach,
} from '../application/approval-queue';
import {
  actorOnlyApprovers,
  type ApproverDirectory,
} from '../ports/approver-directory';
import type { ApprovalRules } from '../ports/approval-rules';
import type {
  ToolRuleWriter,
  ToolRuleWriterDecorator,
} from '../ports/tool-rules';
import type { WaitingRunNotices } from '../ports/approval-links';
import { QuestionInboxService } from '../application/question-inbox';
import { ExecutionService } from '../application/execution-service';
import { ConversationGrantReach } from '../application/conversation-grant-reach';
import { InteractionService } from '../application/interaction-service';
import { RunActivityService } from '../application/run-activity-service';
import { RunContentService } from '../application/run-content-service';
import { RunFollowupsService } from '../application/run-followups-service';
import { RunTimelineService } from '../application/run-timeline-service';
import { SessionInteractionService } from '../application/session-interactions';
import { SessionRunService } from '../application/session-run-service';
import { SharedArtifactService } from '../application/shared-artifacts';
import { SharedCitationService } from '../application/shared-citations';
import { WorkspaceService } from '../application/workspace-service';
import { createSessionRunLookup } from '../../../libs/repositories/agent-harness/session-erasure';
import type { ErasureJobStore } from '../ports/session-erasure';
import type { ExecutionStore } from '../ports/execution-store';
import { RunStreamService } from '../projection/stream-service';
import { RunSpendReader } from '../application/run-spend';
import type { interfaces } from 'inversify';
import { boundOrNull, HarnessBinder } from './context';
import {
  AGENT_HARNESS_APPROVAL_QUEUE,
  AGENT_HARNESS_APPROVAL_QUEUE_REACH,
  AGENT_HARNESS_APPROVAL_RULES,
  AGENT_HARNESS_APPROVER_LINK_NOTICES,
  AGENT_HARNESS_APPROVERS,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_RUN_ACTIVITY,
  AGENT_HARNESS_RUN_CONTENT,
  AGENT_HARNESS_RUN_FOLLOWUPS,
  AGENT_HARNESS_RUN_STREAMS,
  AGENT_HARNESS_QUESTION_INBOX,
  AGENT_HARNESS_RUN_TIMELINES,
  AGENT_HARNESS_SESSION_ERASURES,
  AGENT_HARNESS_SESSION_INTERACTIONS,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
  AGENT_HARNESS_SHARED_ARTIFACTS,
  AGENT_HARNESS_SHARED_CITATIONS,
  AGENT_HARNESS_TOOL_RULE_WRITER,
  AGENT_HARNESS_WORKSPACES,
} from './tokens';

export const bindRunContent: HarnessBinder = ({
  container,
  journal,
  logger,
}) => {
  if (container.isBound(AGENT_HARNESS_RUN_CONTENT)) return;
  container
    .bind<RunContentService>(AGENT_HARNESS_RUN_CONTENT)
    .toDynamicValue(
      (context) =>
        new RunContentService(
          context.container.get<ExecutionService>(
            AGENT_HARNESS_EXECUTION_SERVICE,
          ),
          journal.cipher,
          journal.payloads,
          logger,
        ),
    )
    .inSingletonScope();
};

export const bindSessionRuns: HarnessBinder = ({
  container,
  dependencies,
  journal,
  budgets,
  admissionStore,
}) => {
  if (!container.isBound(AGENT_HARNESS_SESSION_RUN_SERVICE)) {
    container
      .bind<SessionRunService>(AGENT_HARNESS_SESSION_RUN_SERVICE)
      .toDynamicValue(
        (context) =>
          new SessionRunService(
            context.container.get<ExecutionService>(
              AGENT_HARNESS_EXECUTION_SERVICE,
            ),
            dependencies.sessions,
            context.container.get<ErasureJobStore<StoreSession>>(
              AGENT_HARNESS_SESSION_ERASURES,
            ),
            admissionStore,
          ),
      )
      .inSingletonScope();
  }
  if (!container.isBound(AGENT_HARNESS_RUN_TIMELINES)) {
    container
      .bind<RunTimelineService>(AGENT_HARNESS_RUN_TIMELINES)
      .toDynamicValue((context) => {
        const runs = context.container.get<SessionRunService>(
          AGENT_HARNESS_SESSION_RUN_SERVICE,
        );
        return new RunTimelineService({
          runs,
          journal: journal.store,
          journals: journal.service,
          workspaces: context.container.get<WorkspaceService>(
            AGENT_HARNESS_WORKSPACES,
          ),
          ledger: budgets,
          usage: { access: runs, runs: createSessionRunLookup() },
        });
      })
      .inSingletonScope();
  }
};

/** The rules beyond the usual one an edition bound for the session's decisions, if any. */
const rulesOf = (
  container: interfaces.Container,
): { rules?: ApprovalRules } => {
  const rules = boundOrNull<ApprovalRules>(
    container,
    AGENT_HARNESS_APPROVAL_RULES,
  );
  return rules !== null ? { rules } : {};
};

/** Where "Don't ask again" saves a rule: an edition's writer over the dependencies' when it bound one. */
export const toolRulesOf = (
  container: interfaces.Container,
  base: ToolRuleWriter | undefined,
): { toolRules?: ToolRuleWriter } => {
  const decorate = boundOrNull<ToolRuleWriterDecorator>(
    container,
    AGENT_HARNESS_TOOL_RULE_WRITER,
  );
  const toolRules = decorate !== null ? decorate(base) : base;
  return toolRules !== undefined ? { toolRules } : {};
};

/** Who besides a run's actor decides its approvals: an edition's directory if it bound one, else the dependencies'. */
export const bindApprovers: HarnessBinder = ({ container, dependencies }) => {
  if (container.isBound(AGENT_HARNESS_APPROVERS)) return;
  container
    .bind<ApproverDirectory>(AGENT_HARNESS_APPROVERS)
    .toConstantValue(dependencies.approvers ?? actorOnlyApprovers);
};

export const bindInteractions: HarnessBinder = ({
  container,
  dependencies,
  journal,
  logger,
}) => {
  const transcriptReads = new ChainTranscriptReadAudit<StoreSession>(
    journal.audit,
    inDurableTransaction,
  );
  if (!container.isBound(AGENT_HARNESS_SESSION_INTERACTIONS)) {
    container
      .bind<SessionInteractionService>(AGENT_HARNESS_SESSION_INTERACTIONS)
      .toDynamicValue((context) => {
        const executions = context.container.get<ExecutionService>(
          AGENT_HARNESS_EXECUTION_SERVICE,
        );
        const interactions = new InteractionService(
          executions,
          dependencies.wakeup ?? undefined,
          logger,
        );
        return new SessionInteractionService({
          executions,
          interactions,
          grants: new ConversationGrantReach<StoreSession>(
            executions,
            interactions,
            journal.store,
            journal.service,
            inDurableTransaction,
            logger,
          ),
          sessions: dependencies.sessions,
          roles: dependencies.roles,
          approvers: context.container.get<ApproverDirectory>(
            AGENT_HARNESS_APPROVERS,
          ),
          logger,
          ...rulesOf(context.container),
          ...toolRulesOf(context.container, dependencies.toolRules),
        });
      })
      .inSingletonScope();
    // WP115-FU-2: an ask a worker raises that a grant its run already holds answers is answered by Node, the grants'
    // authority, whether the run is the loop or an engine; resolved when an ask is raised, not while binding.
    if (container.isBound(AGENT_HARNESS_EXECUTION_SERVICE)) {
      container
        .get<ExecutionService>(AGENT_HARNESS_EXECUTION_SERVICE)
        .onGrantableAsks((run, interactionIds, commandId) =>
          container
            .get<SessionInteractionService>(AGENT_HARNESS_SESSION_INTERACTIONS)
            .answerHeldGrants(run, interactionIds, commandId),
        );
    }
  }
  if (!container.isBound(AGENT_HARNESS_APPROVAL_QUEUE)) {
    container
      .bind<ApprovalQueueService>(AGENT_HARNESS_APPROVAL_QUEUE)
      .toDynamicValue(
        (context) =>
          new ApprovalQueueService(
            context.container.get<ExecutionStore>(
              AGENT_HARNESS_EXECUTION_STORE,
            ),
            context.container.get<SessionInteractionService>(
              AGENT_HARNESS_SESSION_INTERACTIONS,
            ),
            context.container.get<RunTimelineService>(
              AGENT_HARNESS_RUN_TIMELINES,
            ),
            transcriptReads.record,
            boundOrNull<ApprovalQueueReach>(
              context.container,
              AGENT_HARNESS_APPROVAL_QUEUE_REACH,
            ) ?? ownRunsOnly,
          ),
      )
      .inSingletonScope();
  }
  // AP-08: what an edition sends when a commit makes a run wait for a person; resolved when it waits, not while binding.
  if (
    container.isBound(AGENT_HARNESS_APPROVER_LINK_NOTICES) &&
    container.isBound(AGENT_HARNESS_EXECUTION_SERVICE)
  ) {
    container
      .get<ExecutionService>(AGENT_HARNESS_EXECUTION_SERVICE)
      .onRunWaiting((run) => {
        void container
          .get<WaitingRunNotices>(AGENT_HARNESS_APPROVER_LINK_NOTICES)
          .runWaiting({
            orgId: run.orgId,
            runId: run.runId,
            revision: run.revision,
          });
      });
  }
  if (!container.isBound(AGENT_HARNESS_QUESTION_INBOX)) {
    container
      .bind<QuestionInboxService>(AGENT_HARNESS_QUESTION_INBOX)
      .toDynamicValue(
        (context) =>
          new QuestionInboxService(
            context.container.get<ExecutionStore>(
              AGENT_HARNESS_EXECUTION_STORE,
            ),
            dependencies.sessions,
            context.container.get<RunTimelineService>(
              AGENT_HARNESS_RUN_TIMELINES,
            ),
          ),
      )
      .inSingletonScope();
  }
};

export const bindRunViews: HarnessBinder = ({
  container,
  dependencies,
  journal,
  journalTail,
  logger,
  budgets,
}) => {
  const activity = dependencies.activity;
  if (
    activity !== undefined &&
    !container.isBound(AGENT_HARNESS_RUN_ACTIVITY)
  ) {
    container
      .bind<RunActivityService>(AGENT_HARNESS_RUN_ACTIVITY)
      .toDynamicValue(
        (context) =>
          new RunActivityService({
            runs: context.container.get<SessionRunService>(
              AGENT_HARNESS_SESSION_RUN_SERVICE,
            ),
            executions: context.container.get<ExecutionService>(
              AGENT_HARNESS_EXECUTION_SERVICE,
            ),
            journal: journal.store,
            journals: journal.service,
            tail: journalTail(),
            bus: activity.bus,
            setting: activity.setting,
            content: context.container.get<RunContentService>(
              AGENT_HARNESS_RUN_CONTENT,
            ),
            logger,
          }),
      )
      .inSingletonScope();
  }
  if (!container.isBound(AGENT_HARNESS_RUN_FOLLOWUPS)) {
    container
      .bind<RunFollowupsService>(AGENT_HARNESS_RUN_FOLLOWUPS)
      .toDynamicValue(
        (context) =>
          new RunFollowupsService({
            runs: context.container.get<SessionRunService>(
              AGENT_HARNESS_SESSION_RUN_SERVICE,
            ),
            executions: context.container.get<ExecutionService>(
              AGENT_HARNESS_EXECUTION_SERVICE,
            ),
            bus: activity?.bus ?? null,
            logger,
          }),
      )
      .inSingletonScope();
  }
  const conversationReader = dependencies.conversationReader;
  if (
    conversationReader === undefined ||
    container.isBound(AGENT_HARNESS_RUN_STREAMS)
  ) {
    return;
  }
  container.bind<RunStreamService>(AGENT_HARNESS_RUN_STREAMS).toConstantValue(
    new RunStreamService({
      journal: journal.store,
      journals: journal.service,
      runJournal: journal.runJournal,
      tail: journalTail(),
      conversations: conversationReader,
      liveBus: dependencies.liveBus ?? null,
      logger,
      sharedCitations: container.get<SharedCitationService>(
        AGENT_HARNESS_SHARED_CITATIONS,
      ),
      sharedArtifacts: container.get<SharedArtifactService>(
        AGENT_HARNESS_SHARED_ARTIFACTS,
      ),
      spend: new RunSpendReader(budgets),
    }),
  );
};
