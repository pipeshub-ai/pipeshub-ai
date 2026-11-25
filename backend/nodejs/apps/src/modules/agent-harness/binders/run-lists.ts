import type { StoreSession } from '../../../libs/repositories/agent-harness/durable-transaction';
import { inDurableTransaction } from '../../../libs/repositories/agent-harness/durable-transaction';
import { conversationSeenMarks } from '../../../libs/repositories/agent-harness/conversation-seen';
import { ConversationGrantService } from '../application/conversation-grants';
import type { ExecutionService } from '../application/execution-service';
import { JobBriefService } from '../application/job-briefs';
import { RunListService } from '../application/run-lists';
import type { RunTimelineService } from '../application/run-timeline-service';
import type { SessionRunService } from '../application/session-run-service';
import type { ExecutionStore } from '../ports/execution-store';
import type { RunLists } from '../ports/run-lists';
import type { HarnessBinder } from './context';
import {
  AGENT_HARNESS_CONVERSATION_GRANTS,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_JOB_BRIEFS,
  AGENT_HARNESS_RUN_LISTS,
  AGENT_HARNESS_RUN_TIMELINES,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
} from './tokens';

/**
 * UI-B1 (ADR-UI-01, ADR-GRANT-01 D8): the reads the background-agents interface needs: a person's runs and jobs
 * across conversations with their chat list's state and seen marks (G1), a job's brief (G4), and a conversation's
 * grants with their revocation (G5). Each needs only the run and journal stores every deployment has. A binding made
 * before this binder (an edition's) is kept.
 */
export const bindRunLists: HarnessBinder = ({
  container,
  dependencies,
  journal,
  logger,
}) => {
  const sessionRuns = (): SessionRunService =>
    container.get<SessionRunService>(AGENT_HARNESS_SESSION_RUN_SERVICE);
  if (!container.isBound(AGENT_HARNESS_RUN_LISTS)) {
    const names = dependencies.auditNames;
    container
      .bind<RunListService>(AGENT_HARNESS_RUN_LISTS)
      .toDynamicValue(
        (context) =>
          new RunListService({
            runs: context.container.get<ExecutionStore & RunLists>(
              AGENT_HARNESS_EXECUTION_STORE,
            ),
            seen: dependencies.seenMarks ?? conversationSeenMarks(),
            access: sessionRuns(),
            inputs: journal.runInput,
            ...(names !== undefined
              ? { names: { chats: names.chats, agents: names.agents ?? null } }
              : {}),
          }),
      )
      .inSingletonScope();
  }
  if (!container.isBound(AGENT_HARNESS_JOB_BRIEFS)) {
    container
      .bind<JobBriefService>(AGENT_HARNESS_JOB_BRIEFS)
      .toDynamicValue(
        (context) =>
          new JobBriefService({
            runs: sessionRuns(),
            executions: context.container.get<ExecutionService>(
              AGENT_HARNESS_EXECUTION_SERVICE,
            ),
            subjects: context.container.get<ExecutionStore>(
              AGENT_HARNESS_EXECUTION_STORE,
            ),
            jobs: context.container.get<ExecutionStore & RunLists>(
              AGENT_HARNESS_EXECUTION_STORE,
            ),
            inputs: journal.runInput,
            ...(context.container.isBound(AGENT_HARNESS_RUN_TIMELINES)
              ? {
                  timelines: context.container.get<RunTimelineService>(
                    AGENT_HARNESS_RUN_TIMELINES,
                  ),
                }
              : {}),
          }),
      )
      .inSingletonScope();
  }
  if (!container.isBound(AGENT_HARNESS_CONVERSATION_GRANTS)) {
    container
      .bind<ConversationGrantService<StoreSession>>(
        AGENT_HARNESS_CONVERSATION_GRANTS,
      )
      .toDynamicValue(
        (context) =>
          new ConversationGrantService<StoreSession>({
            access: sessionRuns(),
            executions: context.container.get<ExecutionService>(
              AGENT_HARNESS_EXECUTION_SERVICE,
            ),
            journal: journal.store,
            journals: journal.service,
            transaction: inDurableTransaction,
            members: dependencies.auditNames?.members ?? null,
            ...(logger !== undefined ? { logger } : {}),
          }),
      )
      .inSingletonScope();
  }
};
