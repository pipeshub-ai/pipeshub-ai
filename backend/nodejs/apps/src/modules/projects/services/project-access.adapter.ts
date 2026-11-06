import { getProjectRepository } from '../../../libs/repositories/project';
import { isValidId } from '../../../libs/repositories/ids';
import {
  BadRequestError,
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { CanonicalRole, atLeast } from '../../authz/domain/ladder';
import { projectRole } from '../../authz/domain/project-rules';
import { Subject } from '../../authz/domain/types';
import { projectFactsOf } from '../../authz/loaders/project.loader';
import { IProjectAccessPort } from '../../authz/ports/project-access.port';
import {
  ProjectAccess,
  ProjectDto,
  ProjectRole,
} from '../types/project.interfaces';
import { ProjectService } from './project.service';

const PROJECT_ROLES: readonly ProjectRole[] = [
  'none',
  'viewer',
  'editor',
  'owner',
];

function asProjectRole(role: CanonicalRole): ProjectRole {
  return PROJECT_ROLES.includes(role as ProjectRole)
    ? (role as ProjectRole)
    : 'none';
}

export function projectRoleFor(
  project: Parameters<typeof projectFactsOf>[0],
  subject: Subject,
): ProjectRole {
  return asProjectRole(projectRole(projectFactsOf(project), subject));
}

export class ProjectServiceAccessAdapter implements IProjectAccessPort {
  orgHasTeamMembers(orgId: string): Promise<boolean> {
    return ProjectService.orgHasTeamMembers(orgId);
  }

  async roleOf(
    subject: Subject,
    projectId: string,
  ): Promise<{ role: ProjectRole; project: ProjectDto } | null> {
    if (!isValidId(projectId)) {
      return null;
    }
    const project = await getProjectRepository().getLive(projectId);
    if (!project) {
      return null;
    }
    const role = projectRoleFor(project, subject);
    return role === 'none' ? null : { role, project };
  }

  /** Same as `assertAtLeast` but also returns the resolved role; an invalid id is a 400, as before. */
  async access(
    subject: Subject,
    projectId: string,
    min: ProjectRole,
  ): Promise<ProjectAccess> {
    if (!isValidId(projectId)) {
      throw new BadRequestError('Invalid project ID format');
    }
    const found = await this.roleOf(subject, projectId);
    if (!found) {
      throw new NotFoundError('Project not found');
    }
    if (!atLeast(found.role, min)) {
      throw new ForbiddenError(
        `This action needs the ${min} role on the project`,
      );
    }
    return found;
  }

  async assertAtLeast(
    subject: Subject,
    projectId: string,
    min: ProjectRole,
  ): Promise<ProjectDto> {
    return (await this.access(subject, projectId, min)).project;
  }

  accessibleProjectIds(subject: Subject): Promise<readonly string[]> {
    return ProjectService.getAccessibleProjectIds(subject.orgId, subject.userId, [
      ...subject.teamIds,
    ]);
  }
}
