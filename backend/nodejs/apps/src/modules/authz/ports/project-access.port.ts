import {
  IProjectDocument,
  ProjectRole,
} from '../../projects/types/project.interfaces';
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
}
