import type { StoreSession } from '../../../libs/repositories/agent-harness/durable-transaction';
import { SessionParticipantJournal } from '../application/session-participant-journal';
import { SharedArtifactService } from '../application/shared-artifacts';
import { SharedCitationService } from '../application/shared-citations';
import { SessionParticipantRecorder } from '../ports/session-participants';
import { EngineSignInService } from '../application/engine-signin-service';
import { boundOrNull, HarnessBinder } from './context';
import {
  AGENT_HARNESS_ENGINE_SIGNINS,
  AGENT_HARNESS_SESSION_PARTICIPANTS,
  AGENT_HARNESS_SHARED_ARTIFACTS,
  AGENT_HARNESS_SHARED_CITATIONS,
} from './tokens';

export const bindSharing: HarnessBinder = ({
  container,
  dependencies,
  journal,
  logger,
}) => {
  if (!container.isBound(AGENT_HARNESS_SHARED_CITATIONS)) {
    container
      .bind<SharedCitationService>(AGENT_HARNESS_SHARED_CITATIONS)
      .toConstantValue(
        new SharedCitationService(dependencies.recordAccess ?? null, logger),
      );
  }
  if (!container.isBound(AGENT_HARNESS_SHARED_ARTIFACTS)) {
    container
      .bind<SharedArtifactService>(AGENT_HARNESS_SHARED_ARTIFACTS)
      .toConstantValue(
        new SharedArtifactService(dependencies.artifactAccess ?? null, logger),
      );
  }
  if (!container.isBound(AGENT_HARNESS_SESSION_PARTICIPANTS)) {
    container
      .bind<SessionParticipantRecorder<StoreSession>>(
        AGENT_HARNESS_SESSION_PARTICIPANTS,
      )
      .toConstantValue(
        new SessionParticipantJournal(
          journal.store,
          journal.service,
          logger,
          (orgId, sessionId) =>
            boundOrNull<EngineSignInService>(
              container,
              AGENT_HARNESS_ENGINE_SIGNINS,
            )?.sessionShared(orgId, sessionId) ?? Promise.resolve(),
        ),
      );
  }
};
