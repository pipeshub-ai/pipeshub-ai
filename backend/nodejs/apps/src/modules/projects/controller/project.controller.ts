import { Response, NextFunction } from 'express';
import mongoose from 'mongoose';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { AppConfig } from '../../tokens_manager/config/config';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { IAMServiceCommand } from '../../../libs/commands/iam/iam.service.command';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { ChatSession } from '../../enterprise_search/schema/chat.session.schema';
import {
  CreateProjectInput,
  ProjectService,
  UpdateProjectInput,
} from '../services/project.service';
import { ProjectKnowledgeBaseService } from '../services/project-kb.service';
import { IProject, IProjectDocument, ProjectRole } from '../types/project.interfaces';
import { resolveCallerTeamIds } from '../utils/team-membership';
import { OBJECT_ID_REGEX } from '../../../libs/validators/zod-primitives';
import { callerIdentityOf } from '../../../libs/types/caller-identity';
import {
  conversationContextOf,
  listFilterOf,
} from '../../enterprise_search/services/collaboration/http/conversation-context';
import { redactRecipients } from '../../enterprise_search/services/collaboration/http/list-access';
import {
  listSelect,
  sharedListDecorator,
} from '../../enterprise_search/services/collaboration/http/list-fields';
import { teamDirectoryFor } from '../../user_management/services/team-directory.service';
import { IAuditWriter } from '../../../libs/audit/audit.writer';
import { COLLAB_FLAG_KEYS } from '../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../configuration_manager/services/platform-feature-flags.service';

/** Enrich a Mongoose project document with the caller's computed role for JSON responses. */
function projectWithRole(
  project: IProjectDocument,
  role: ProjectRole,
): IProject & { role: ProjectRole } {
  return { ...(project.toObject() as unknown as IProject), role };
}

type ProjectRouteHandler = (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => Promise<void>;

export const createProject = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId as string;
    const orgId = req.user?.orgId as string;
    const project = await ProjectService.create(
      orgId,
      userId,
      req.body as CreateProjectInput,
    );
    res.status(201).json({ project: projectWithRole(project, 'owner') });
  } catch (error) {
    next(error);
  }
};

export const listProjects =
  (appConfig: AppConfig): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      // ValidationMiddleware replaces req.query with the Zod-parsed/coerced
      // result, so page/limit are already numbers and includeArchived a boolean.
      const { page, limit, search, scope, includeArchived, isArchived } =
        req.query as unknown as {
          page: number;
          limit: number;
          search?: string;
          scope: 'mine' | 'shared' | 'all';
          includeArchived: boolean;
          isArchived?: boolean;
        };
      const callerTeamIds = await resolveCallerTeamIds(req, appConfig);
      const { projects, totalCount } = await ProjectService.list(
        orgId,
        userId,
        { page, limit, search, scope, includeArchived, isArchived },
        callerTeamIds,
      );
      res.status(200).json({
        projects,
        pagination: {
          page,
          limit,
          totalCount,
          totalPages: Math.ceil(totalCount / limit),
        },
      });
    } catch (error) {
      next(error);
    }
  };

export const getProjectById =
  (appConfig: AppConfig): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const callerTeamIds = await resolveCallerTeamIds(req, appConfig);
      const { role, project } = await ProjectService.assertAccess(
        orgId,
        userId,
        projectId,
        'viewer',
        callerTeamIds,
      );
      res.status(200).json({ project: projectWithRole(project, role) });
    } catch (error) {
      next(error);
    }
  };

/** What `PATCH /:projectId` needs to honour `projectChatAccess`; without it the field is ignored. */
export interface ProjectChatAccessDeps {
  flags: IFeatureFlags;
  audit: IAuditWriter;
}

export const updateProject =
  (
    appConfig: AppConfig,
    chatAccess?: ProjectChatAccessDeps,
  ): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const callerTeamIds = await resolveCallerTeamIds(req, appConfig);
      const { projectChatAccess, ...rest } = req.body as UpdateProjectInput;
      const honourChatAccess =
        chatAccess !== undefined &&
        (await chatAccess.flags.isEnabled(COLLAB_FLAG_KEYS.collaborativeChats));
      const patch: UpdateProjectInput = honourChatAccess
        ? { ...rest, projectChatAccess }
        : rest;
      const project = await ProjectService.update(
        orgId,
        userId,
        projectId,
        patch,
        callerTeamIds,
        async (change, saved) =>
          chatAccess?.audit.record({
            orgId: new mongoose.Types.ObjectId(orgId),
            actorUserId: new mongoose.Types.ObjectId(userId),
            action: 'project.chatAccessChanged',
            targetType: 'project',
            targetId: projectId,
            before: { projectChatAccess: change.before },
            after: { projectChatAccess: change.after },
            aclVersion: saved.aclVersion,
            requestId: req.context?.requestId,
          }),
      );
      const role = ProjectService.computeRole(
        project,
        userId,
        orgId,
        callerTeamIds,
      );
      if (patch.visibility !== undefined) {
        await ProjectKnowledgeBaseService.enqueueSync(project);
      }
      res.status(200).json({ project: projectWithRole(project, role) });
    } catch (error) {
      next(error);
    }
  };

/**
 * DELETE /:projectId — deletes the linked hidden Collection *before*
 * soft-deleting the project (mirrors `ProjectService.softDelete`'s own
 * "unlink before mark-deleted" ordering): a crash between the two steps
 * leaves an active project pointing at an already-deleted KB, which
 * `ensureLinkedKb`'s self-heal recreates on next use, rather than an
 * inaccessible deleted project holding an orphaned KB forever.
 */
export const deleteProject =
  (appConfig: AppConfig): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const project = await ProjectService.loadForDelete(orgId, userId, projectId);
      if (project) {
        await ProjectKnowledgeBaseService.deleteLinkedKb(
          appConfig,
          req.headers as Record<string, string>,
          project,
        );
        await ProjectService.softDelete(orgId, userId, projectId);
      }
      res.status(200).json({ message: 'Project deleted successfully' });
    } catch (error) {
      next(error);
    }
  };

export const archiveProject =
  (appConfig: AppConfig): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const callerTeamIds = await resolveCallerTeamIds(req, appConfig);
      const project = await ProjectService.setArchived(
        orgId,
        userId,
        projectId,
        true,
        callerTeamIds,
      );
      const role = ProjectService.computeRole(project, userId, orgId, callerTeamIds);
      res.status(200).json({ project: projectWithRole(project, role) });
    } catch (error) {
      next(error);
    }
  };

export const unarchiveProject =
  (appConfig: AppConfig): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const callerTeamIds = await resolveCallerTeamIds(req, appConfig);
      const project = await ProjectService.setArchived(
        orgId,
        userId,
        projectId,
        false,
        callerTeamIds,
      );
      const role = ProjectService.computeRole(project, userId, orgId, callerTeamIds);
      res.status(200).json({ project: projectWithRole(project, role) });
    } catch (error) {
      next(error);
    }
  };

export const pinProject =
  (appConfig: AppConfig): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const callerTeamIds = await resolveCallerTeamIds(req, appConfig);
      const project = await ProjectService.setPinned(
        orgId,
        userId,
        projectId,
        true,
        callerTeamIds,
      );
      const role = ProjectService.computeRole(project, userId, orgId, callerTeamIds);
      res.status(200).json({ project: projectWithRole(project, role) });
    } catch (error) {
      next(error);
    }
  };

export const unpinProject =
  (appConfig: AppConfig): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const callerTeamIds = await resolveCallerTeamIds(req, appConfig);
      const project = await ProjectService.setPinned(
        orgId,
        userId,
        projectId,
        false,
        callerTeamIds,
      );
      const role = ProjectService.computeRole(project, userId, orgId, callerTeamIds);
      res.status(200).json({ project: projectWithRole(project, role) });
    } catch (error) {
      next(error);
    }
  };

/**
 * GET /:projectId/conversations — chat + agent sessions in this project that
 * the caller may see: rows they own, plus rows with `projectVisibility:
 * 'project'` if they have at least viewer access to the project itself, plus
 * (collaborative chats on) rows shared to them directly or through a team.
 * Access to the project has already been asserted, so a private chat that
 * belongs to a *different* project member never leaks here.
 */
export const getProjectConversations =
  (appConfig: AppConfig): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const callerTeamIds = await resolveCallerTeamIds(req, appConfig);
      await ProjectService.assertAccess(
        orgId,
        userId,
        projectId,
        'viewer',
        callerTeamIds,
      );

      const { page, limit } = req.query as unknown as {
        page: number;
        limit: number;
      };
      const skip = (page - 1) * limit;

      const filter = {
        $and: [
          listFilterOf(req),
          { projectId: new mongoose.Types.ObjectId(projectId) },
        ],
      };

      const [conversations, totalCount] = await Promise.all([
        ChatSession.find(filter)
          .sort({ lastActivityAt: -1, _id: -1 })
          .skip(skip)
          .limit(limit)
          .select(listSelect(conversationContextOf(req)))
          .lean()
          .exec(),
        ChatSession.countDocuments(filter),
      ]);

      const ctx = conversationContextOf(req);
      const decorate = await sharedListDecorator(ctx, conversations);
      res.status(200).json({
        conversations: conversations.map((row) =>
          decorate(redactRecipients(ctx, row)),
        ),
        pagination: {
          page,
          limit,
          totalCount,
          totalPages: Math.ceil(totalCount / limit),
        },
      });
    } catch (error) {
      next(error);
    }
  };

export const listProjectMembers =
  (appConfig: AppConfig): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const callerTeamIds = await resolveCallerTeamIds(req, appConfig);
      const members = await ProjectService.listMembers(
        orgId,
        userId,
        projectId,
        callerTeamIds,
      );
      res.status(200).json({ members });
    } catch (error) {
      next(error);
    }
  };

/**
 * PUT /:projectId/members — mirrors `shareConversationById`'s IAM
 * existence check (es_controller.ts) so a `user` member cannot be added
 * for a userId that doesn't exist in this org's IAM service, and a `team`
 * member cannot be added for a team id that doesn't exist in this org's
 * graph.
 */
export const upsertProjectMembers =
  (appConfig: AppConfig): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const { members } = req.body as {
        members: Array<{
          principalId: string;
          principalType?: 'user' | 'team';
          role: 'viewer' | 'editor';
        }>;
      };

      await Promise.all(
        members.map(async (member) => {
          if (member.principalType === 'team') {
            const exists = await teamDirectoryFor(appConfig).exists(
              member.principalId,
              callerIdentityOf(req),
            );
            if (!exists) {
              throw new BadRequestError(`Team not found: ${member.principalId}`);
            }
            return;
          }
          try {
            const iamCommand = new IAMServiceCommand({
              uri: `${appConfig.iamBackend}/api/v1/users/${encodeURIComponent(member.principalId)}`,
              method: HttpMethod.GET,
              headers: req.headers as Record<string, string>,
            });
            const userResponse = await iamCommand.execute();
            if (userResponse.statusCode !== 200) {
              throw new BadRequestError(
                `User not found: ${member.principalId}`,
              );
            }
          } catch {
            throw new BadRequestError(`User not found: ${member.principalId}`);
          }
        }),
      );

      const project = await ProjectService.upsertMembers(
        orgId,
        userId,
        projectId,
        members,
      );
      await ProjectKnowledgeBaseService.enqueueSync(project);

      res.status(200).json({ members: project.members });
    } catch (error) {
      next(error);
    }
  };

export const removeProjectMember =
  (_appConfig: AppConfig): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId, memberUserId } = req.params as {
        projectId: string;
        memberUserId: string;
      };
      const { principalType } = req.query as {
        principalType?: 'user' | 'team';
      };
      const effectivePrincipalType = principalType ?? 'user';
      if (
        effectivePrincipalType === 'user' &&
        !OBJECT_ID_REGEX.test(memberUserId)
      ) {
        throw new BadRequestError('Invalid member ID format');
      }
      const project = await ProjectService.removeMember(
        orgId,
        userId,
        projectId,
        memberUserId,
        effectivePrincipalType,
      );
      await ProjectKnowledgeBaseService.enqueueSync(project);

      res.status(200).json({ members: project.members });
    } catch (error) {
      next(error);
    }
  };

/**
 * POST /:projectId/knowledge-base — lazily creates (or returns) the
 * project's hidden linked Collection. Editor-or-above: the Files card lets
 * editors upload, and the first upload is what normally triggers this.
 */
export const ensureProjectKnowledgeBase =
  (appConfig: AppConfig): ProjectRouteHandler =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const callerTeamIds = await resolveCallerTeamIds(req, appConfig);
      await ProjectService.assertAccess(
        orgId,
        userId,
        projectId,
        'editor',
        callerTeamIds,
      );
      const kbId = await ProjectKnowledgeBaseService.ensureLinkedKb(
        appConfig,
        req.headers as Record<string, string>,
        orgId,
        projectId,
      );
      res.status(200).json({ kbId });
    } catch (error) {
      next(error);
    }
  };
