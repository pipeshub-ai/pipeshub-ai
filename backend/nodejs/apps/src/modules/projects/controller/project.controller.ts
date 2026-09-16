import { Response, NextFunction } from 'express';
import mongoose from 'mongoose';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { IAMServiceCommand } from '../../../libs/commands/iam/iam.service.command';
import {
  compressImageIfNeeded,
  handleBackendError,
  SUPPORTED_CHAT_ATTACHMENT_MIMETYPES,
} from '../../enterprise_search/controller/es_controller';
import { ChatSession } from '../../enterprise_search/schema/chat.session.schema';
import {
  CreateProjectInput,
  ProjectService,
  UpdateProjectInput,
} from '../services/project.service';
import { IProject, PROJECT_FILE_LIMITS } from '../types/project.interfaces';

const logger = Logger.getInstance({ service: 'ProjectsService' });

/**
 * Grants or revokes READER permission edges on a set of project file
 * records for a set of users, via the same Python
 * `POST|DELETE /api/v1/chat/attachments/permissions` endpoint the chat
 * sharing flow uses (see `shareConversationById`/`unshareConversationById`,
 * es_controller.ts). Best-effort: a failure here never fails the caller's
 * membership/file mutation, it only means the graph permission edge lags
 * behind the Mongo membership list until the next sync.
 */
async function syncFilePermissions(
  appConfig: AppConfig,
  req: AuthenticatedUserRequest,
  recordIds: string[],
  userIds: string[],
  method: typeof HttpMethod.POST | typeof HttpMethod.DELETE,
): Promise<void> {
  if (recordIds.length === 0 || userIds.length === 0) return;
  try {
    const commandOptions: AICommandOptions = {
      uri: `${appConfig.aiBackend}/api/v1/chat/attachments/permissions`,
      method,
      headers: {
        ...(req.headers as Record<string, string>),
        'Content-Type': 'application/json',
      },
      body: { userIds, recordIds },
    };
    await new AIServiceCommand(commandOptions).execute();
  } catch (error) {
    logger.warn('Failed to sync project file permissions', {
      recordIds,
      userIds,
      method,
      error: error instanceof Error ? error.message : String(error),
    });
  }
}

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
    res.status(201).json({ project });
  } catch (error) {
    next(error);
  }
};

export const listProjects = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId as string;
    const orgId = req.user?.orgId as string;
    // ValidationMiddleware replaces req.query with the Zod-parsed/coerced
    // result, so page/limit are already numbers and includeArchived a boolean.
    const { page, limit, search, scope, includeArchived } =
      req.query as unknown as {
        page: number;
        limit: number;
        search?: string;
        scope: 'mine' | 'shared' | 'all';
        includeArchived: boolean;
      };
    const { projects, totalCount } = await ProjectService.list(orgId, userId, {
      page,
      limit,
      search,
      scope,
      includeArchived,
    });
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

export const getProjectById = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId as string;
    const orgId = req.user?.orgId as string;
    const { projectId } = req.params as { projectId: string };
    const { role, project } = await ProjectService.assertAccess(
      orgId,
      userId,
      projectId,
      'viewer',
    );
    res.status(200).json({
      project: { ...(project.toObject() as unknown as IProject), role },
    });
  } catch (error) {
    next(error);
  }
};

export const updateProject = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId as string;
    const orgId = req.user?.orgId as string;
    const { projectId } = req.params as { projectId: string };
    const project = await ProjectService.update(
      orgId,
      userId,
      projectId,
      req.body as UpdateProjectInput,
    );
    res.status(200).json({ project });
  } catch (error) {
    next(error);
  }
};

export const deleteProject = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId as string;
    const orgId = req.user?.orgId as string;
    const { projectId } = req.params as { projectId: string };
    await ProjectService.softDelete(orgId, userId, projectId);
    res.status(200).json({ message: 'Project deleted successfully' });
  } catch (error) {
    next(error);
  }
};

export const archiveProject = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId as string;
    const orgId = req.user?.orgId as string;
    const { projectId } = req.params as { projectId: string };
    const project = await ProjectService.setArchived(
      orgId,
      userId,
      projectId,
      true,
    );
    res.status(200).json({ project });
  } catch (error) {
    next(error);
  }
};

export const unarchiveProject = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId as string;
    const orgId = req.user?.orgId as string;
    const { projectId } = req.params as { projectId: string };
    const project = await ProjectService.setArchived(
      orgId,
      userId,
      projectId,
      false,
    );
    res.status(200).json({ project });
  } catch (error) {
    next(error);
  }
};

export const pinProject = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId as string;
    const orgId = req.user?.orgId as string;
    const { projectId } = req.params as { projectId: string };
    const project = await ProjectService.setPinned(
      orgId,
      userId,
      projectId,
      true,
    );
    res.status(200).json({ project });
  } catch (error) {
    next(error);
  }
};

export const unpinProject = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId as string;
    const orgId = req.user?.orgId as string;
    const { projectId } = req.params as { projectId: string };
    const project = await ProjectService.setPinned(
      orgId,
      userId,
      projectId,
      false,
    );
    res.status(200).json({ project });
  } catch (error) {
    next(error);
  }
};

/**
 * GET /:projectId/conversations — chat + agent sessions in this project that
 * the caller may see: rows they own, plus rows with `projectVisibility:
 * 'project'` if they have at least viewer access to the project itself.
 * Access to the project has already been asserted, so a private chat that
 * belongs to a *different* project member never leaks here.
 */
export const getProjectConversations = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId as string;
    const orgId = req.user?.orgId as string;
    const { projectId } = req.params as { projectId: string };
    await ProjectService.assertAccess(orgId, userId, projectId, 'viewer');

    const { page, limit } = req.query as unknown as {
      page: number;
      limit: number;
    };
    const skip = (page - 1) * limit;

    const filter = {
      orgId: new mongoose.Types.ObjectId(orgId),
      projectId: new mongoose.Types.ObjectId(projectId),
      isDeleted: false,
      $or: [
        { userId: new mongoose.Types.ObjectId(userId) },
        { projectVisibility: 'project' },
      ],
    };

    const [conversations, totalCount] = await Promise.all([
      ChatSession.find(filter)
        .sort({ lastActivityAt: -1, _id: -1 })
        .skip(skip)
        .limit(limit)
        .select('-__v')
        .lean()
        .exec(),
      ChatSession.countDocuments(filter),
    ]);

    res.status(200).json({
      conversations,
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

/**
 * POST /:projectId/files — proxies each upload through the same Python
 * `POST /api/v1/chat/attachments/upload` pipeline as chat attachments (see
 * `uploadChatAttachments`, es_controller.ts) so project files land in the
 * graph + blob store identically, then records the returned refs on the
 * project document (capped — see PROJECT_FILE_LIMITS).
 */
export const uploadProjectFiles =
  (
    appConfig: AppConfig,
  ): ((
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) => Promise<void>) =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const { project } = await ProjectService.assertAccess(
        orgId,
        userId,
        projectId,
        'editor',
      );

      const files = (req.files as Express.Multer.File[] | undefined) ?? [];
      if (!Array.isArray(files) || files.length === 0) {
        throw new BadRequestError('At least one file is required');
      }
      if (project.files.length + files.length > PROJECT_FILE_LIMITS.MAX_FILES) {
        throw new BadRequestError(
          `A project can have at most ${PROJECT_FILE_LIMITS.MAX_FILES} files`,
        );
      }
      const invalidFile = files.find(
        (file) =>
          !SUPPORTED_CHAT_ATTACHMENT_MIMETYPES.has(
            (file.mimetype || '').toLowerCase(),
          ),
      );
      if (invalidFile) {
        throw new BadRequestError(
          `Unsupported attachment type: ${invalidFile.originalname}`,
        );
      }

      const normalizedFiles = await Promise.all(
        files.map((file) => compressImageIfNeeded(file)),
      );

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/chat/attachments/upload`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: {
          conversationId: null,
          attachments: normalizedFiles.map((file) => ({
            fileName: file.fileName,
            mimeType: file.mimeType,
            size: file.size,
            contentBase64: file.buffer.toString('base64'),
          })),
        },
      };

      const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponse = await aiServiceCommand.execute();
      if (!aiResponse.data || aiResponse.statusCode !== 200) {
        throw new BadRequestError(
          `Failed to upload attachment(s): ${aiResponse.msg ?? 'Unknown error'}`,
        );
      }

      const uploadResponse = aiResponse.data as {
        attachments?: Array<{
          recordId: string;
          recordName?: string;
          mimeType?: string;
          extension?: string;
          virtualRecordId?: string;
        }>;
      };
      const uploaded = uploadResponse.attachments ?? [];

      let updatedProject = project;
      for (let i = 0; i < uploaded.length; i++) {
        const ref = uploaded[i];
        if (!ref) continue;
        updatedProject = await ProjectService.addFile(
          orgId,
          userId,
          projectId,
          {
            recordId: ref.recordId,
            recordName: ref.recordName,
            mimeType: ref.mimeType,
            extension: ref.extension,
            virtualRecordId: ref.virtualRecordId,
            sizeBytes: normalizedFiles[i]?.size,
          },
        );
      }

      const newRecordIds = uploaded
        .map((ref) => ref.recordId)
        .filter((id): id is string => Boolean(id));
      const memberUserIds = updatedProject.members
        .filter((m) => m.principalType === 'user')
        .map((m) => m.principalId.toString());
      await syncFilePermissions(
        appConfig,
        req,
        newRecordIds,
        memberUserIds,
        HttpMethod.POST,
      );

      res.status(200).json({ files: updatedProject.files });
    } catch (error) {
      next(handleBackendError(error, 'Upload Project Files'));
    }
  };

/**
 * DELETE /:projectId/files/:recordId — removes the ref from the project and
 * best-effort cleans up the graph record via the same Python delete used by
 * chat attachments. Failures deleting upstream are logged, not surfaced —
 * the ref is already gone from the project either way.
 */
export const deleteProjectFile =
  (
    appConfig: AppConfig,
  ): ((
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) => Promise<void>) =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId, recordId } = req.params as {
        projectId: string;
        recordId: string;
      };
      const project = await ProjectService.removeFile(
        orgId,
        userId,
        projectId,
        recordId,
      );

      try {
        const aiUrl = `${appConfig.aiBackend}/api/v1/chat/attachments/${encodeURIComponent(recordId)}`;
        const allowedHeaders = new Set(['content-type', 'authorization']);
        const forwardHeaders: Record<string, string> = Object.fromEntries(
          Object.entries(req.headers as Record<string, string>).filter(([k]) =>
            allowedHeaders.has(k.toLowerCase()),
          ),
        );
        const resp = await fetch(aiUrl, { method: 'DELETE', headers: forwardHeaders });
        if (!resp.ok) {
          logger.warn('Upstream project file deletion returned non-OK status', {
            projectId,
            recordId,
            status: resp.status,
          });
        }
      } catch (cleanupError) {
        logger.error('Failed to delete upstream project file record', {
          projectId,
          recordId,
          error:
            cleanupError instanceof Error
              ? cleanupError.message
              : String(cleanupError),
        });
      }

      res.status(200).json({ files: project.files });
    } catch (error) {
      next(error);
    }
  };

export const listProjectMembers = async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const userId = req.user?.userId as string;
    const orgId = req.user?.orgId as string;
    const { projectId } = req.params as { projectId: string };
    const members = await ProjectService.listMembers(orgId, userId, projectId);
    res.status(200).json({ members });
  } catch (error) {
    next(error);
  }
};

/**
 * PUT /:projectId/members — mirrors `shareConversationById`'s IAM
 * existence check (es_controller.ts) so a member cannot be added for a
 * userId that doesn't exist in this org's IAM service.
 */
export const upsertProjectMembers =
  (
    appConfig: AppConfig,
  ): ((
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) => Promise<void>) =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId } = req.params as { projectId: string };
      const { members } = req.body as {
        members: Array<{ principalId: string; role: 'viewer' | 'editor' }>;
      };

      await Promise.all(
        members.map(async (member) => {
          try {
            const iamCommand = new IAMServiceCommand({
              uri: `${appConfig.iamBackend}/api/v1/users/${member.principalId}`,
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

      const fileRecordIds = project.files.map((f) => f.recordId);
      const newMemberUserIds = members.map((m) => m.principalId);
      await syncFilePermissions(
        appConfig,
        req,
        fileRecordIds,
        newMemberUserIds,
        HttpMethod.POST,
      );

      res.status(200).json({ members: project.members });
    } catch (error) {
      next(error);
    }
  };

export const removeProjectMember =
  (
    appConfig: AppConfig,
  ): ((
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) => Promise<void>) =>
  async (req, res, next): Promise<void> => {
    try {
      const userId = req.user?.userId as string;
      const orgId = req.user?.orgId as string;
      const { projectId, memberUserId } = req.params as {
        projectId: string;
        memberUserId: string;
      };
      const project = await ProjectService.removeMember(
        orgId,
        userId,
        projectId,
        memberUserId,
      );

      const fileRecordIds = project.files.map((f) => f.recordId);
      await syncFilePermissions(
        appConfig,
        req,
        fileRecordIds,
        [memberUserId],
        HttpMethod.DELETE,
      );

      res.status(200).json({ members: project.members });
    } catch (error) {
      next(error);
    }
  };
