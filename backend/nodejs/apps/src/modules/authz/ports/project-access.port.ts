import { ProjectRole } from '../../projects/types/project.interfaces';
import type { IProjectDocument } from '../../projects/schema/project.schema';
import { Subject } from '../domain/types';

export interface IProjectAccessPort {
  /** Null when the project is missing, deleted, or the subject has no role on it. */
  roleOf(
    subject: Subject,
    projectId: string,
  ): Promise<{ role: ProjectRole; project: IProjectDocument } | null>;
  /** NotFoundError when the subject cannot see the project, ForbiddenError when the role is below `min`. */
  assertAtLeast(
    subject: Subject,
    projectId: string,
    min: ProjectRole,
  ): Promise<IProjectDocument>;
  accessibleProjectIds(subject: Subject): Promise<readonly string[]>;
  /** Whether some live project of the org has a team member; absent, read as false. */
  orgHasTeamMembers?(orgId: string): Promise<boolean>;
}
