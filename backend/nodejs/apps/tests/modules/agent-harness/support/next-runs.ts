import { ActorReadmission } from '../../../../src/modules/agent-harness/application/actor-readmission';
import type { RunAdmissionService } from '../../../../src/modules/agent-harness/application/run-admission-service';
import type { ActorAuthority } from '../../../../src/modules/agent-harness/ports/schedules';

/** Every sender is a person in good standing whose credential is their own bearer (as the record fakes key it). */
export const bearerActors: Pick<ActorAuthority, 'authorize'> = {
  authorize: async (_orgId, actorId) => ({ authorization: `Bearer ${actorId}` }),
};

/** Next runs re-admitted as production does: as their sender, by the admission's own policy steps. */
export const readmissionBy = (
  admission: () => Pick<RunAdmissionService, 'readmit'>,
  actors: Pick<ActorAuthority, 'authorize'> = bearerActors,
): ActorReadmission => new ActorReadmission(actors, admission);
