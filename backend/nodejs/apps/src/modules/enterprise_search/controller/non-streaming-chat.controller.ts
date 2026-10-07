import { NextFunction, Response } from 'express';
import mongoose, { Types } from 'mongoose';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { BadRequestError } from '../../../libs/errors/http.errors';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  validateNoFormatSpecifiers,
  validateNoXSS,
} from '../../../utils/xss-sanitization';
import { ProjectService } from '../../projects/services/project.service';
import { IProjectDocument } from '../../projects/types/project.interfaces';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  CONVERSATION_STATUS,
  EXCLUDE_AGENT,
  ONLY_AGENT,
} from '../constants/constants';
import {
  IChatSession,
  IChatSessionDocument,
} from '../types/conversation.interfaces';
import { buildAiChatRequest, ChatTarget } from '../utils/ai-chat-payload';
import {
  completeTurn,
  continueConversation,
  CONVERSATION_ID_HEADER,
  openConversation,
} from '../utils/non-streaming-chat';
import {
  loadProjectForSession,
  resolveProjectLink,
} from '../utils/project-context';
import { buildUserQueryMessage, extractModelInfo } from '../utils/utils';
import { hydrateScopedRequestAsUser } from '../utils/scoped-request';

const logger = Logger.getInstance({ service: 'Non-streaming chat' });

type ChatRequest = AuthenticatedUserRequest | AuthenticatedServiceRequest;
type TurnMode = 'create' | 'continue';
type TurnHandler = (
  req: ChatRequest,
  res: Response,
  next: NextFunction,
) => Promise<void>;

const targetOf = (req: ChatRequest): ChatTarget =>
  req.params.agentKey
    ? { kind: 'agent', agentKey: req.params.agentKey }
    : { kind: 'assistant' };

const sessionFields = (
  target: ChatTarget,
  body: Record<string, unknown>,
  userId: Types.ObjectId,
  orgId: Types.ObjectId,
): Partial<IChatSession> => ({
  orgId,
  userId,
  initiator: userId,
  title: String(body.query).slice(0, 100),
  lastActivityAt: Date.now(),
  status: CONVERSATION_STATUS.INPROGRESS,
  modelInfo: extractModelInfo(body),
  ...(target.kind === 'agent'
    ? {
        agentKey: target.agentKey,
        sessionType: 'agent',
        conversationSource: 'agent_chat',
      }
    : { sessionType: 'chat' }),
});

/**
 * One handler for the four non-streaming chat routes; `mode` picks between
 * starting a conversation and adding a turn, `:agentKey` between the
 * assistant and a saved agent. Scoped-token callers (`/internal/...`) are
 * resolved to their user first, as the streaming internal routes do.
 */
const nonStreamingTurn =
  (appConfig: AppConfig, mode: TurnMode) =>
  async (
    req: ChatRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const startTime = Date.now();
    const requestId = req.context?.requestId;
    try {
      const body = req.body as Record<string, unknown>;
      const query = body.query;
      // The route validators require it; the guard keeps an unvalidated
      // mount from starting a turn with no question.
      if (typeof query !== 'string' || query.trim() === '') {
        throw new BadRequestError('Query is required');
      }
      validateNoXSS(query, 'query');
      validateNoFormatSpecifiers(query, 'query');

      await hydrateScopedRequestAsUser(req, appConfig);
      const { userId, orgId } = (req as AuthenticatedUserRequest).user as {
        userId: Types.ObjectId;
        orgId: Types.ObjectId;
      };

      const target = targetOf(req);
      const userMessage = buildUserQueryMessage(
        query,
        body.appliedFilters as never,
        body.chatMode as string | undefined,
        body.attachments as never,
      );

      let conversation: IChatSessionDocument;
      let previousConversations: unknown[];
      let project: IProjectDocument | undefined;
      if (mode === 'create') {
        const link = await resolveProjectLink(
          String(orgId),
          String(userId),
          body,
        );
        conversation = await openConversation(
          {
            ...sessionFields(target, body, userId, orgId),
            ...(link.projectId
              ? {
                  projectId: new mongoose.Types.ObjectId(link.projectId),
                  projectVisibility: link.projectVisibility,
                }
              : {}),
          },
          userMessage,
        );
        previousConversations = Array.isArray(body.previousConversations)
          ? body.previousConversations
          : [];
        project = link.project;
      } else {
        // Project context always comes from the session row, never the
        // request body — a follow-up turn cannot move itself into a project.
        const continued = await continueConversation(
          {
            _id: req.params.conversationId,
            orgId,
            userId,
            isDeleted: false,
            ...(target.kind === 'agent'
              ? { agentKey: target.agentKey, ...ONLY_AGENT }
              : EXCLUDE_AGENT),
          },
          userMessage,
        );
        conversation = continued.conversation;
        previousConversations = continued.previousConversations;
        project = await loadProjectForSession(
          String(conversation.orgId),
          String(conversation.userId),
          conversation.projectId,
        );
      }

      const conversationId = String(conversation._id);
      res.setHeader(CONVERSATION_ID_HEADER, conversationId);
      if (conversation.projectId) {
        void ProjectService.touchActivity(conversation.projectId.toString());
      }

      const turn = await completeTurn({
        target,
        conversation,
        aiBackend: appConfig.aiBackend,
        request: buildAiChatRequest(target, body, {
          conversationId,
          previousConversations,
          isNewConversation: mode === 'create',
          project,
        }),
        headers: req.headers as Record<string, string>,
        modelInfo: extractModelInfo(body),
        requestId,
      });

      const meta = {
        requestId,
        timestamp: new Date().toISOString(),
        duration: Date.now() - startTime,
      };
      if (mode === 'create') {
        res
          .status(HTTP_STATUS.CREATED)
          .json({ conversation: turn.conversation, meta });
      } else {
        res.status(HTTP_STATUS.OK).json({
          conversation: turn.conversation,
          recordsUsed: turn.recordsUsed,
          meta: { ...meta, recordsUsed: turn.recordsUsed },
        });
      }
    } catch (error: unknown) {
      logger.error('Non-streaming chat turn failed', {
        requestId,
        mode,
        conversationId: req.params.conversationId,
        error: error instanceof Error ? error.message : String(error),
        duration: Date.now() - startTime,
      });
      next(error);
    }
  };

/** `POST /conversations/create` and `/conversations/internal/create`. */
export const createConversation = (appConfig: AppConfig): TurnHandler =>
  nonStreamingTurn(appConfig, 'create');

/** `POST /conversations/:conversationId/messages` and its `/internal/` twin. */
export const addMessage = (appConfig: AppConfig): TurnHandler =>
  nonStreamingTurn(appConfig, 'continue');

/** `POST /agents/:agentKey/conversations`. */
export const createAgentConversation = (appConfig: AppConfig): TurnHandler =>
  nonStreamingTurn(appConfig, 'create');

/** `POST /agents/:agentKey/conversations/:conversationId/messages`. */
export const addMessageToAgentConversation = (
  appConfig: AppConfig,
): TurnHandler => nonStreamingTurn(appConfig, 'continue');
