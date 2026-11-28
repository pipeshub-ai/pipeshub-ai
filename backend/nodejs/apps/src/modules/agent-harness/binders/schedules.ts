import { orgHarnessOn, transactionsAvailable } from '../config';
import type { ControlPlaneTransaction } from '../../control-plane';
import { BrokerTriggerSource } from '../adapters/messaging/broker-trigger-source';
import { createRunStatusReader } from '../../../libs/repositories/agent-harness/run-status';
import { createScheduleStore } from '../../../libs/repositories/agent-harness/schedules';
import { ActorAgentVersions } from '../application/actor-agent-versions';
import type { CodingEngineService } from '../application/coding-engine-service';
import type { ExecutionService } from '../application/execution-service';
import { RunAdmissionService } from '../application/run-admission-service';
import type { RunTimelineService } from '../application/run-timeline-service';
import { ScheduleFirer } from '../application/schedule-firer';
import { HarnessScheduleRunner } from '../application/harness-schedule-runner';
import {
  ClassicScheduleRunner,
  type ClassicTurns,
} from '../application/classic-schedule-runner';
import {
  RepositoryChatConversations,
  RepositoryClassicConversations,
} from '../adapters/classic-schedule-conversations';
import { getConversationRepository } from '../../../libs/repositories/conversation';
import { InboxAwareScheduleNotifier } from '../application/inbox-notice-policy';
import type { InboxPreferenceStore } from '../ports/inbox';
import { ScheduleService } from '../application/schedule-service';
import {
  ChatScheduleService,
  JournalScheduleMarks,
} from '../application/chat-schedules';
import { inDurableTransaction } from '../../../libs/repositories/agent-harness/durable-transaction';
import type { ExecutionStore } from '../ports/execution-store';
import { RunAdmissionScheduledRuns } from '../application/scheduled-run-admission';
import { RELEASE_TRIGGER_SETTINGS } from '../application/release-run-settings';
import type { ScheduleReach, TriggerSettings } from '../ports/schedules';
import { boundOrNull, HarnessBinder } from './context';
import { runBudgetCeilingOf } from './cost-ports';
import type { ScheduledWaitNotices } from '../ports/schedules';
import {
  AGENT_HARNESS_SCHEDULED_WAIT_NOTICES,
  AGENT_HARNESS_CHAT_SCHEDULES,
  AGENT_HARNESS_CODING_ENGINES,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_INBOX_STORE,
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_RUN_TIMELINES,
  AGENT_HARNESS_SCHEDULE_FIRER,
  AGENT_HARNESS_CLASSIC_TURNS,
  AGENT_HARNESS_SCHEDULES,
  CONTROL_PLANE_TRANSACTION,
  AGENT_HARNESS_TRIGGER_SETTINGS,
  AGENT_HARNESS_SCHEDULE_REACH,
} from './tokens';

export const bindSchedules: HarnessBinder = ({
  container,
  dependencies,
  logger,
  journal,
}) => {
  const schedules = dependencies.schedules;
  if (schedules === undefined || container.isBound(AGENT_HARNESS_SCHEDULES)) {
    return;
  }
  const store = schedules.store ?? createScheduleStore(journal.runInput);
  const triggers =
    boundOrNull<TriggerSettings>(container, AGENT_HARNESS_TRIGGER_SETTINGS) ??
    RELEASE_TRIGGER_SETTINGS;
  container
    .bind<ScheduleFirer>(AGENT_HARNESS_SCHEDULE_FIRER)
    .toDynamicValue((context) => {
      const classicTurns = boundOrNull<ClassicTurns>(
        context.container,
        AGENT_HARNESS_CLASSIC_TURNS,
      );
      const firer: ScheduleFirer = new ScheduleFirer({
        store,
        harness: new HarnessScheduleRunner(
          new RunAdmissionScheduledRuns(
            context.container.get<RunAdmissionService>(
              AGENT_HARNESS_RUN_ADMISSION_SERVICE,
            ),
          ),
          createRunStatusReader(),
          context.container.isBound(AGENT_HARNESS_RUN_TIMELINES)
            ? context.container.get<RunTimelineService>(
                AGENT_HARNESS_RUN_TIMELINES,
              )
            : null,
        ),
        ...(classicTurns !== null
          ? {
              classic: new ClassicScheduleRunner(
                classicTurns,
                new RepositoryClassicConversations(getConversationRepository),
                Date.now,
                dependencies.agentAccess,
              ),
            }
          : {}),
        actors: schedules.actors,
        setting: schedules.setting,
        settings: triggers,
        harnessOn: (orgId) => orgHarnessOn(orgId),
        ...(dependencies.recordAccess !== undefined
          ? { records: dependencies.recordAccess }
          : {}),
        // G2 (ADR-INBOX-01): the actor's mutes and pause apply to their workflows' notices.
        ...(schedules.notifier !== undefined
          ? {
              notifier: context.container.isBound(AGENT_HARNESS_INBOX_STORE)
                ? new InboxAwareScheduleNotifier(
                    schedules.notifier,
                    context.container.get<InboxPreferenceStore>(
                      AGENT_HARNESS_INBOX_STORE,
                    ),
                  )
                : schedules.notifier,
            }
          : {}),
        // AP-10: what a bound feature sends the actor of a waiting run besides the inbox notice.
        ...(context.container.isBound(AGENT_HARNESS_SCHEDULED_WAIT_NOTICES)
          ? {
              waitNotices: context.container.get<ScheduledWaitNotices>(
                AGENT_HARNESS_SCHEDULED_WAIT_NOTICES,
              ),
            }
          : {}),
        ...(schedules.connectorEvents !== undefined
          ? {
              connectorEvents: new BrokerTriggerSource({
                createConsumer: schedules.connectorEvents.consumer,
                streams: schedules.connectorEvents.streams,
                deliver: (event) => firer.deliver(event),
                clock: () => store.now(),
                logger,
              }),
            }
          : {}),
        logger,
      });
      return firer;
    })
    .inSingletonScope();
  container
    .bind<ScheduleService>(AGENT_HARNESS_SCHEDULES)
    .toDynamicValue(
      (context) =>
        new ScheduleService({
          store,
          firer: context.container.get<ScheduleFirer>(
            AGENT_HARNESS_SCHEDULE_FIRER,
          ),
          actors: schedules.actors,
          setting: schedules.setting,
          transaction: context.container.get<ControlPlaneTransaction>(
            CONTROL_PLANE_TRANSACTION,
          ),
          triggers,
          budgets: runBudgetCeilingOf(context.container),
          ...(schedules.slackBots !== undefined
            ? { slackBots: schedules.slackBots }
            : {}),
          ...(schedules.agents !== undefined
            ? { agents: schedules.agents }
            : {}),
          reach: boundOrNull<ScheduleReach>(
            context.container,
            AGENT_HARNESS_SCHEDULE_REACH,
          ),
          conversations: new RepositoryChatConversations(
            getConversationRepository,
          ),
          storeReady: () => transactionsAvailable(),
          agentVersions: new ActorAgentVersions(
            schedules.actors,
            dependencies.agentAccess,
          ),
          logger,
        }),
    )
    .inSingletonScope();
  if (
    container.isBound(AGENT_HARNESS_EXECUTION_SERVICE) &&
    container.isBound(AGENT_HARNESS_EXECUTION_STORE)
  ) {
    container
      .bind<ChatScheduleService>(AGENT_HARNESS_CHAT_SCHEDULES)
      .toDynamicValue((context) => {
        const runs = context.container.get<ExecutionStore>(
          AGENT_HARNESS_EXECUTION_STORE,
        );
        return new ChatScheduleService({
          runs: context.container.get<ExecutionService>(
            AGENT_HARNESS_EXECUTION_SERVICE,
          ),
          vias: {
            via: async (orgId, runId) =>
              (await runs.readClaimSubject(orgId, runId))?.via ?? null,
          },
          schedules: context.container.get<ScheduleService>(
            AGENT_HARNESS_SCHEDULES,
          ),
          store,
          actors: schedules.actors,
          marks: new JournalScheduleMarks(
            journal.store,
            journal.service,
            inDurableTransaction,
          ),
          ...(context.container.isBound(AGENT_HARNESS_CODING_ENGINES)
            ? {
                engines: context.container.get<CodingEngineService>(
                  AGENT_HARNESS_CODING_ENGINES,
                ),
              }
            : {}),
          logger,
        });
      })
      .inSingletonScope();
  }
  // A fired run's end tells its actor and frees a firing queued behind it (TR-06, TR-07) at once; the
  // scheduler's watch is the backstop. The firer is resolved when a run ends, not while binding.
  if (container.isBound(AGENT_HARNESS_EXECUTION_SERVICE)) {
    const executions = container.get<ExecutionService>(
      AGENT_HARNESS_EXECUTION_SERVICE,
    );
    executions.onRunEnded((run) => {
      try {
        void container
          .get<ScheduleFirer>(AGENT_HARNESS_SCHEDULE_FIRER)
          .runEnded(run);
      } catch (error) {
        logger?.warn('schedule.run_end_failed', {
          orgId: run.orgId,
          runId: run.runId,
          errorType: error instanceof Error ? error.name : typeof error,
        });
      }
    });
    // UIB5-FU-1: a run that starts waiting on its person, or goes on, is told to its owner now, not at the watch.
    executions.onRunStatusChanged((run) => {
      try {
        void container
          .get<ScheduleFirer>(AGENT_HARNESS_SCHEDULE_FIRER)
          .runMoved(run);
      } catch (error) {
        logger?.warn('schedule.run_move_failed', {
          orgId: run.orgId,
          runId: run.runId,
          errorType: error instanceof Error ? error.name : typeof error,
        });
      }
    });
  }
};
