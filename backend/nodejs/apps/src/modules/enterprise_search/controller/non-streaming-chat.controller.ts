import { NextFunction, Response } from 'express';
import { Types } from 'mongoose';
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
import { refuseRunWhileShuttingDown } from '../utils/active-runs';
import { IChatSessionDocument } from '../types/conversation.interfaces';
import { buildAiChatRequest, ChatTarget } from '../utils/ai-chat-payload';
import {
  completeTurn,
  CONVERSATION_ID_HEADER,
} from '../utils/non-streaming-chat';
import { createFirstTurn } from '../utils/first-turn';
import { resolveProjectLink } from '../utils/project-context';
import { extractModelInfo } from '../utils/utils';
import { hydrateScopedRequestAsUser } from '../utils/scoped-request';
import {
  aiRequestBody,
  appendFollowUpQuery,
  followUpContext,
  FollowUpBody,
} from '../utils/follow-up-turn';
import { RunLostError } from '../services/conversation/domain/errors';
import { LeaseLostError } from '../services/conversation/turn/run-lease';
import { conversationExtensions } from '../services/conversation/conversation-extensions';
import {
  FirstSendPreparation,
  TurnResponder,
} from '../services/conversation/turn/turn-extension';
import { ConversationTurnDeps } from '../services/conversation/turn/turn-deps';
import {
  holdLease,
  openTurnGate,
  outcomeForError,
  TurnGate,
  unleasedGate,
} from '../services/conversation/turn/turn-gate';
import {
  outcomeForStatus,
  TurnOutcome,
} from '../services/conversation/turn/turn-lifecycle';
import { TurnRun } from '../services/conversation/turn/turn-run';
import { RUN_ID_HEADER } from '../utils/turn-stream';

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

/**
 * One handler for the four non-streaming chat routes; `mode` picks between
 * starting a conversation and adding a turn, `:agentKey` between the
 * assistant and a saved agent. Scoped-token callers (`/internal/...`) are
 * resolved to their user first, as the streaming internal routes do.
 */
const nonStreamingTurn =
  (appConfig: AppConfig, mode: TurnMode, deps: ConversationTurnDeps) =>
  async (
    req: ChatRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const startTime = Date.now();
    const requestId = req.context?.requestId;
    // `unstarted` until the user's message is stored, so a request that fails earlier leaves the session idle.
    let outcome: TurnOutcome = 'unstarted';
    let gate: TurnGate | undefined;
    const turns = conversationExtensions().turns;
    try {
      refuseRunWhileShuttingDown();
      const body = req.body as Record<string, unknown>;
      const query = body.query;
      // The route validators require it; the guard keeps an unvalidated
      // mount from starting a turn with no question.
      if (typeof query !== 'string' || query.trim() === '') {
        throw new BadRequestError('Query is required');
      }
      validateNoXSS(turns.queryForValidation(query), 'query');
      validateNoFormatSpecifiers(query, 'query');

      await hydrateScopedRequestAsUser(req, appConfig);
      const { userId, orgId } = (req as AuthenticatedUserRequest).user as {
        userId: Types.ObjectId;
        orgId: Types.ObjectId;
      };

      const target = targetOf(req);
      let preparation: FirstSendPreparation | undefined;
      if (mode === 'create') {
        preparation = await turns.prepareFirstSend({
          req: req as AuthenticatedUserRequest,
          deps,
          target,
          body: body as FollowUpBody,
          mode: 'plain',
        });
      }
      const responder: TurnResponder =
        preparation?.responder ??
        turns.responderFor(req as AuthenticatedUserRequest, target);
      // A lost lease needs no action here: the answer's fenced write is the one that must fail.
      const onLost = (): void => {
        logger.warn('Run lost its lease during a non-streaming turn', {
          requestId,
          conversationId: req.params.conversationId,
        });
      };
      if (mode !== 'create') {
        gate = openTurnGate(req as AuthenticatedUserRequest, onLost);
      }
      const { attachments } = body as FollowUpBody;

      let conversation: IChatSessionDocument;
      let previousConversations: unknown[];
      let project: IProjectDocument | undefined;
      let aiPayload: Record<string, unknown> | undefined;
      let run: TurnRun;
      if (mode === 'create') {
        const link = await resolveProjectLink(
          String(orgId),
          String(userId),
          body,
        );
        const turn = await createFirstTurn({
          deps,
          req: req as AuthenticatedUserRequest,
          target,
          userId,
          orgId,
          body: body as FollowUpBody,
          attachments,
          link,
          preparation: preparation as FirstSendPreparation,
        });
        if (
          await turns.replyWithoutRun({
            req: req as AuthenticatedUserRequest,
            res,
            deps,
            turn,
            mode: 'plain',
            projectId: link.projectId,
            startTime,
            requestId,
          })
        ) {
          return;
        }
        gate = turn.run.lease
          ? holdLease(turn.run.lease, onLost)
          : unleasedGate();
        outcome = 'failed';
        run = turn.run;
        conversation = turn.conversation;
        previousConversations = Array.isArray(body.previousConversations)
          ? body.previousConversations
          : [];
        project = link.project;
      } else {
        // Project context always comes from the session row, never the
        // request body — a follow-up turn cannot move itself into a project.
        const turn = await appendFollowUpQuery({
          req: req as AuthenticatedUserRequest,
          target,
          userId,
          orgId,
          body: body as FollowUpBody,
          attachments,
          gate: gate as TurnGate,
        });
        outcome = 'failed';
        run = turn.run;
        conversation = turn.conversation;
        ({ previousConversations, project, aiPayload } = await followUpContext(
          req as AuthenticatedUserRequest,
          turn,
          deps,
        ));
      }

      const conversationId = String(conversation._id);
      res.setHeader(CONVERSATION_ID_HEADER, conversationId);
      if (run.lease) res.setHeader(RUN_ID_HEADER, run.lease.runId);
      if (conversation.projectId) {
        void ProjectService.touchActivity(conversation.projectId.toString());
      }

      const turn = await completeTurn({
        target,
        conversation,
        aiBackend: appConfig.aiBackend,
        request: buildAiChatRequest(
          responder.target,
          aiRequestBody(body as FollowUpBody, attachments, run),
          {
            conversationId,
            previousConversations,
            isNewConversation: mode === 'create',
            extraPayload: {
              ...turns.aiPayloadFields(conversation),
              ...aiPayload,
            },
            project,
            guestAgent: responder.respondingAgentKey !== undefined,
          },
        ),
        headers: req.headers as Record<string, string>,
        modelInfo: extractModelInfo(body),
        requestId,
        run,
      });
      outcome = outcomeForStatus(turn.conversation.status as string);
      // Release before replying: a client that sends again on seeing the answer must not meet our lease.
      await gate?.settle(outcome);

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
      const ended = outcomeForError(error);
      await gate?.settle(ended === 'failed' ? outcome : ended);
      next(error instanceof LeaseLostError ? new RunLostError() : error);
    } finally {
      await gate?.settle(outcome);
    }
  };

/** `POST /conversations/create` and `/conversations/internal/create`. */
export const createConversation = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
): TurnHandler => nonStreamingTurn(appConfig, 'create', deps);

/** `POST /conversations/:conversationId/messages` and its `/internal/` twin. */
export const addMessage = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
): TurnHandler => nonStreamingTurn(appConfig, 'continue', deps);

/** `POST /agents/:agentKey/conversations`. */
export const createAgentConversation = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
): TurnHandler => nonStreamingTurn(appConfig, 'create', deps);

/** `POST /agents/:agentKey/conversations/:conversationId/messages`. */
export const addMessageToAgentConversation = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
): TurnHandler => nonStreamingTurn(appConfig, 'continue', deps);
