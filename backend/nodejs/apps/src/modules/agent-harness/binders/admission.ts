import type { OperatorStops } from '../ports/operator-stops';
import type { AdmissionQuotaCapsSource } from '../ports/admission-quota-caps';
import type { RunPlacement } from '../ports/run-placement';
import { HttpEngineAvailability } from '../adapters/http/http-engine-availability';
import { HttpInlineClaimHint } from '../adapters/http/inline-claim-hint';
import { harnessTracerFromEnv } from '../adapters/telemetry/otlp-http-tracer';
import { ActorReadmission } from '../application/actor-readmission';
import { HarnessCapabilityService } from '../application/capability-service';
import { CodingEngineService } from '../application/coding-engine-service';
import { SessionRepositoryService } from '../application/session-repository-service';
import { HarnessAuthority } from '../application/harness-authority';
import { MemoryService } from '../application/memory-service';
import { ModelAccessService } from '../application/model-access-service';
import { NextRunService } from '../application/next-run-service';
import type { OrgCapabilityRepair } from '../application/org-capability-repair';
import { RolloutRoutingDecider } from '../application/rollout-routing';
import {
  decidingRouter,
  pinningSteps,
  routingDecider,
} from './control-plane-pin';
import { RoutingCapabilities } from '../application/routing-capabilities';
import {
  CONTROL_PLANE_ME_CAPABILITIES,
  createHeadRevisions,
  type MeCapabilitiesSource,
} from '../../control-plane';
import { RunAdmissionService } from '../application/run-admission-service';
import { SharedCitationService } from '../application/shared-citations';
import { WorkerFeatureAvailability } from '../application/worker-feature-availability';
import { profileExecutor } from '../domain/run-spec';
import { EngineProfileCeilings } from '../ports/engine-profiles';
import { Logger } from '../../../libs/services/logger.service';
import { AgentAccessPolicy } from '../ports/agent-access';
import { boundOrNull, HarnessBinder } from './context';
import { groupAccessOf, withGroupAccess } from './group-access';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_CODING_ENGINES,
  AGENT_HARNESS_SESSION_REPOSITORIES,
  AGENT_HARNESS_CAPABILITIES,
  AGENT_HARNESS_ENGINE_PROFILES,
  AGENT_HARNESS_MEMORIES,
  AGENT_HARNESS_MODEL_ACCESS,
  AGENT_HARNESS_NEXT_RUNS,
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_SHARED_CITATIONS,
  AGENT_HARNESS_WORKER_FEATURES,
  AGENT_HARNESS_AGENT_ACCESS,
  AGENT_HARNESS_RUN_PLACEMENT,
  AGENT_HARNESS_OPERATOR_CONTROLS,
  AGENT_HARNESS_ADMISSION_QUOTA_CAPS,
  CONTROL_PLANE_CAPABILITY_REPAIR,
} from './tokens';
import { questionGoneAnnouncer } from './inbox';

export const bindNextRuns: HarnessBinder = ({
  container,
  dependencies,
  admissionStore,
  logger,
}) => {
  if (container.isBound(AGENT_HARNESS_NEXT_RUNS)) return;
  container.bind<NextRunService>(AGENT_HARNESS_NEXT_RUNS).toConstantValue(
    new NextRunService({
      store: admissionStore,
      readmission: new ActorReadmission(
        dependencies.schedules?.actors ?? null,
        () =>
          container.get<RunAdmissionService>(
            AGENT_HARNESS_RUN_ADMISSION_SERVICE,
          ),
      ),
      collaboratorTurns: dependencies.collaboratorTurns ?? null,
      ...(logger !== undefined ? { logger } : {}),
      // ADR-INBOX-02 D4: a drained message moves its chat on, so a question the chat was left on is gone.
      questionAnswered: questionGoneAnnouncer(container),
      // ADM-25: a drained message is routed as a fresh turn of its sender's would be (with the harness group, G-41).
      routing: withGroupAccess(
        container,
        routingDecider(
          container,
          new RolloutRoutingDecider({
            rollout: dependencies.rollout,
            sessions: dependencies.sessions,
          }),
          logger,
        ),
      ),
    }),
  );
};

/**
 * CPD-10: `/me/capabilities` answers from the decider that routes this
 * process's turns, without the shadow comparison a real turn starts.
 */
export const bindRoutingCapabilities: HarnessBinder = ({
  container,
  dependencies,
}) => {
  if (container.isBound(CONTROL_PLANE_ME_CAPABILITIES)) return;
  container
    .bind<MeCapabilitiesSource>(CONTROL_PLANE_ME_CAPABILITIES)
    .toDynamicValue((context) => {
      const decider = decidingRouter(
        context.container,
        new RolloutRoutingDecider({
          rollout: dependencies.rollout,
          sessions: dependencies.sessions,
          ...(context.container.isBound(AGENT_HARNESS_WORKER_FEATURES)
            ? {
                liveness: context.container.get<WorkerFeatureAvailability>(
                  AGENT_HARNESS_WORKER_FEATURES,
                ),
              }
            : {}),
        }),
      );
      return new RoutingCapabilities({
        decider: withGroupAccess(context.container, decider),
        heads: createHeadRevisions(),
      });
    })
    .inSingletonScope();
};

export const bindRunAdmission: HarnessBinder = ({
  container,
  dependencies,
  admissionStore,
  logger,
}) => {
  if (container.isBound(AGENT_HARNESS_RUN_ADMISSION_SERVICE)) return;
  const inlineClaimer = dependencies.inlineClaimer;
  container
    .bind<RunAdmissionService>(AGENT_HARNESS_RUN_ADMISSION_SERVICE)
    .toDynamicValue(
      (context) =>
        new RunAdmissionService({
          store: admissionStore,
          rollout: dependencies.rollout,
          agentAccess:
            boundOrNull<AgentAccessPolicy>(
              context.container,
              AGENT_HARNESS_AGENT_ACCESS,
            ) ?? dependencies.agentAccess,
          inline:
            inlineClaimer === undefined
              ? undefined
              : new HttpInlineClaimHint(
                  inlineClaimer,
                  () =>
                    context.container
                      .get<HarnessAuthority>(AGENT_HARNESS_AUTHORITY)
                      .workerCredential(),
                  context.container.isBound('Logger')
                    ? context.container.get<Logger>('Logger')
                    : { warn: () => undefined },
                ),
          logger,
          // ADR-INBOX-02: the Inbox is bound after admission, so it is looked up when a question is answered.
          questionAnswered: questionGoneAnnouncer(context.container),
          ...(context.container.isBound(CONTROL_PLANE_CAPABILITY_REPAIR)
            ? {
                capabilityRepair: context.container.get<OrgCapabilityRepair>(
                  CONTROL_PLANE_CAPABILITY_REPAIR,
                ),
              }
            : {}),
          // C4.10: an org's caps on its active runs, for a run admitted without a pin; unbound: the release's quotas.
          ...optionalQuotaCaps(
            boundOrNull<AdmissionQuotaCapsSource>(
              context.container,
              AGENT_HARNESS_ADMISSION_QUOTA_CAPS,
            ),
          ),
          // CPD-5: the control plane pins beside today's decision (CONTROL_PLANE_PIN).
          ...pinningSteps(context.container),
          // CPD-10: and routes the turn, compared in `shadow` and deciding in `on`.
          routing: routingDecider(
            context.container,
            new RolloutRoutingDecider({
              rollout: dependencies.rollout,
              sessions: dependencies.sessions,
              ...(context.container.isBound(AGENT_HARNESS_WORKER_FEATURES)
                ? {
                    liveness: context.container.get<WorkerFeatureAvailability>(
                      AGENT_HARNESS_WORKER_FEATURES,
                    ),
                  }
                : {}),
            }),
            logger,
          ),
          references: dependencies.recordAccess,
          roles: dependencies.roles,
          ...(groupAccessOf(context.container) !== undefined
            ? { groupAccess: groupAccessOf(context.container) }
            : {}),
          ...(context.container.isBound(AGENT_HARNESS_RUN_PLACEMENT)
            ? {
                placement: context.container.get<RunPlacement>(
                  AGENT_HARNESS_RUN_PLACEMENT,
                ),
              }
            : {}),
          ...(dependencies.collaboratorTurns !== undefined
            ? { collaboratorTurns: dependencies.collaboratorTurns }
            : {}),
          // The one record authorization C-6 uses decides labels too (P2.5a).
          turnLabels: context.container.get<SharedCitationService>(
            AGENT_HARNESS_SHARED_CITATIONS,
          ),
          ...(context.container.isBound(AGENT_HARNESS_MEMORIES)
            ? {
                memory: context.container.get<MemoryService>(
                  AGENT_HARNESS_MEMORIES,
                ),
              }
            : {}),
          // One resolver for admission and the claim (ExecutionService): E4a.
          executors: profileExecutor,
          // P5.11: an agent run admitted while a code sandbox is proved needs `agent_code`.
          ...(context.container.isBound(AGENT_HARNESS_CAPABILITIES)
            ? {
                capabilities: context.container.get<HarnessCapabilityService>(
                  AGENT_HARNESS_CAPABILITIES,
                ),
              }
            : {}),
          // DUR2: required only while a live worker advertises it.
          ...(context.container.isBound(AGENT_HARNESS_WORKER_FEATURES)
            ? {
                workerFeatures:
                  context.container.get<WorkerFeatureAvailability>(
                    AGENT_HARNESS_WORKER_FEATURES,
                  ),
                liveness: context.container.get<WorkerFeatureAvailability>(
                  AGENT_HARNESS_WORKER_FEATURES,
                ),
              }
            : {}),
          // UX3: a project's turn and a worker outage route to legacy, read from the conversation.
          sessions: dependencies.sessions,
          // P9: the run's root span when HARNESS_OTEL is on.
          tracer: harnessTracerFromEnv(process.env, logger),
          ...(context.container.isBound(AGENT_HARNESS_ENGINE_PROFILES)
            ? {
                engineProfiles: context.container.get<EngineProfileCeilings>(
                  AGENT_HARNESS_ENGINE_PROFILES,
                ),
              }
            : {}),
          ...(context.container.isBound(AGENT_HARNESS_MODEL_ACCESS)
            ? {
                modelAccess: context.container.get<ModelAccessService>(
                  AGENT_HARNESS_MODEL_ACCESS,
                ),
              }
            : {}),
          ...(context.container.isBound(AGENT_HARNESS_CODING_ENGINES)
            ? {
                codingEngine: context.container.get<CodingEngineService>(
                  AGENT_HARNESS_CODING_ENGINES,
                ),
              }
            : {}),
          ...(context.container.isBound(AGENT_HARNESS_SESSION_REPOSITORIES)
            ? {
                repositories: context.container.get<SessionRepositoryService>(
                  AGENT_HARNESS_SESSION_REPOSITORIES,
                ),
              }
            : {}),
          ...(context.container.isBound(AGENT_HARNESS_OPERATOR_CONTROLS)
            ? {
                operatorControls: context.container.get<OperatorStops>(
                  AGENT_HARNESS_OPERATOR_CONTROLS,
                ),
              }
            : {}),
          // H1: an engine whose image the broker lacks is refused here, not at its first turn.
          ...(dependencies.sandboxBrokerEndpoint !== undefined
            ? {
                engineAvailability: new HttpEngineAvailability(
                  dependencies.sandboxBrokerEndpoint,
                  () =>
                    context.container
                      .get<HarnessAuthority>(AGENT_HARNESS_AUTHORITY)
                      .workerCredential(),
                ),
              }
            : {}),
        }),
    )
    .inSingletonScope();
};

const optionalQuotaCaps = (
  quotaCaps: AdmissionQuotaCapsSource | null,
): { quotaCaps?: AdmissionQuotaCapsSource } =>
  quotaCaps === null ? {} : { quotaCaps };
