import mongoose from 'mongoose';
import {
  BadRequestError,
  ForbiddenError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { CanonicalRole, atLeast } from '../../authz/domain/ladder';
import { projectRole } from '../../authz/domain/rules';
import { Subject } from '../../authz/domain/types';
import {
  ProjectListScope,
  projectAccessFilter,
  projectFactsOf,
} from '../../authz/loaders/project.loader';
import { IProjectAccessPort } from '../../authz/ports/project-access.port';
import { Project } from '../schema/project.schema';
import {
  IProjectDocument,
  ProjectAccess,
  ProjectRole,
} from '../types/project.interfaces';

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
  async roleOf(
    subject: Subject,
    projectId: string,
  ): Promise<{ role: ProjectRole; project: IProjectDocument } | null> {
    if (!mongoose.Types.ObjectId.isValid(projectId)) {
      return null;
    }
    const project = await Project.findOne({ _id: projectId, isDeleted: false });
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
    if (!mongoose.Types.ObjectId.isValid(projectId)) {
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
  ): Promise<IProjectDocument> {
    return (await this.access(subject, projectId, min)).project;
  }

  async accessibleObjectIds(
    subject: Subject,
  ): Promise<mongoose.Types.ObjectId[]> {
    const projects = await Project.find(
      {
        orgId: new mongoose.Types.ObjectId(subject.orgId),
        isDeleted: false,
        $or: projectAccessFilter(subject, 'all' satisfies ProjectListScope),
      },
      { _id: 1 },
    ).lean();
    return projects.map((p) => p._id);
  }

  async accessibleProjectIds(subject: Subject): Promise<readonly string[]> {
    return (await this.accessibleObjectIds(subject)).map((id) => id.toString());
  }
}
