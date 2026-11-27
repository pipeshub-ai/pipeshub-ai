import type { AdmitRunCommand } from '../domain/admission';
import type {
  NextRunReadmission,
  Readmission,
} from '../ports/next-run-readmission';
import type { ActorAuthority } from '../ports/schedules';
import type { RunAdmissionService } from './run-admission-service';
import { admissionRefusalReason } from './scheduled-run-admission';

/**
 * `NextRunReadmission` as its sender: the sender checked and given a
 * short-lived credential of their own (the `ActorAuthority` scheduled runs
 * use), then the admission's own policy steps (`RunAdmissionService.readmit`).
 * Without an actor authority nothing can be decided, so nothing is admitted.
 */
export class ActorReadmission implements NextRunReadmission {
  constructor(
    private readonly actors: Pick<ActorAuthority, 'authorize'> | null,
    private readonly admissions: () => Pick<RunAdmissionService, 'readmit'>,
  ) {}

  async readmit(command: AdmitRunCommand): Promise<Readmission> {
    if (this.actors === null) return { refused: 'unavailable' };
    let actor: Awaited<ReturnType<ActorAuthority['authorize']>>;
    try {
      actor = await this.actors.authorize(command.orgId, command.actorId);
    } catch {
      return { refused: 'unavailable' };
    }
    if ('refused' in actor) return { refused: actor.refused };
    try {
      return {
        command: await this.admissions().readmit(command, actor.authorization),
      };
    } catch (error) {
      return { refused: admissionRefusalReason(error) };
    }
  }
}
