import { NextFunction, Response } from 'express';
import { ProjectService } from '../../projects/services/project.service';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { AICommandOptions } from '../../../libs/commands/ai_service/ai.service.command';
import { BadRequestError } from '../../../libs/errors/http.errors';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { catchAsync } from '../../../libs/middlewares/catch-async.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import { RunLostError } from '../services/conversation/domain/errors';
import { extendedTurnFor } from '../services/conversation/http/conversation-context';
import { LeaseLostError } from '../services/conversation/turn/run-lease';
import { ConversationTurnDeps } from '../services/conversation/turn/turn-deps';
import { conversationExtensions } from '../services/conversation/conversation-extensions';
import {
  holdLease,
  outcomeForError,
  TurnGate,
  unleasedGate,
} from '../services/conversation/turn/turn-gate';
import { AGUI_PROTOCOL, AGUIEventType, frameAGUI } from '../utils/agui';
import { startAIStream } from '../utils/ai-stream';
import {
  buildAiChatRequest,
  ChatTarget,
  parseChatMode,
} from '../utils/ai-chat-payload';
import { userFacingChatError } from '../utils/chat-error-messages';
import { createFirstTurn } from '../utils/first-turn';
import {
  aiRequestBody,
  FollowUpBody,
  turnCallerOf,
} from '../utils/follow-up-turn';
import { resolveProjectLink } from '../utils/project-context';
import { refuseRunWhileShuttingDown } from '../utils/active-runs';
import { attachUpstreamAbort } from '../utils/stream-lifecycle';
import {
  TurnStreamPump,
  writeConversationCreated,
  writeSseHead,
} from '../utils/turn-stream';
import {
  extractModelInfo,
  keepRunAlive,
  StageTimer,
  streamFrameOf,
} from '../utils/utils';

const logger = Logger.getInstance({ service: 'First-send stream' });

type TurnRequest = AuthenticatedUserRequest | AuthenticatedServiceRequest;

/**
 * The streaming first send for a chat (`/stream`) or an agent (`/:agentKey/conversations/stream`):
 * creates the conversation with the caller's first message, relays the AI backend's stream, and
 * saves the answer. Under an extended guard policy the turn extension's checks run and the session
 * may be born holding the run's lease, both before any SSE header; otherwise the stream opens first.
 */
export const firstSendStream = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
  kind: ChatTarget['kind'],
): ((req: TurnRequest, res: Response, next?: NextFunction) => Promise<void>) =>
  catchAsync<TurnRequest>(async (req, res, next): Promise<void> => {
    refuseRunWhileShuttingDown();
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const timer = new StageTimer();
    const body = req.body as FollowUpBody;
    if (!body.query) {
      throw new BadRequestError('Query is required');
    }
    const { agentKey } = req.params;
    const { userId, orgId } = turnCallerOf(req);
    const extended = extendedTurnFor(req);
    const turns = conversationExtensions().turns;
    // Before any SSE header.
    turns.screenFirstSend(req as AuthenticatedUserRequest, body);
    const target: ChatTarget =
      kind === 'agent'
        ? { kind: 'agent', agentKey: agentKey as string }
        : { kind: 'assistant' };

    // The heartbeat and the client's close can fire before the pump exists, so they reach it through this holder.
    const live: { pump?: TurnStreamPump; lost: boolean } = { lost: false };
    let gate: TurnGate = unleasedGate();
    const upstreamAbort = attachUpstreamAbort(res, requestId, () => {
      void live.pump?.onDisconnect();
    });

    try {
      // Without a lease nothing can reject the turn before streaming, so the stream opens first as it always did.
      if (!extended) writeSseHead(res);
      const preparation = await turns.prepareFirstSend({
        req: req as AuthenticatedUserRequest,
        deps,
        target,
        body,
        mode: 'stream',
      });
      const { responder } = preparation;

      const { attachments } = body;
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
        body,
        attachments,
        link,
        preparation,
      });
      const { conversation, run } = turn;
      gate = run.lease
        ? holdLease(run.lease, () => {
            live.lost = true;
            void live.pump?.onLost();
          })
        : unleasedGate();
      const conversationId = String(conversation._id);
      if (
        await turns.replyWithoutRun({
          req: req as AuthenticatedUserRequest,
          res,
          deps,
          turn,
          mode: 'stream',
          projectId: link.projectId,
          startTime,
          requestId,
        })
      ) {
        return;
      }
      logger.debug('Initial conversation created', {
        requestId,
        conversationId,
        userId,
        agentKey,
      });

      // With a lease every 4xx so far was JSON; from here on failures are SSE frames.
      if (extended) writeSseHead(res, run.lease?.runId);
      writeConversationCreated(res, {
        conversationId,
        title: conversation.title || undefined,
        projectId: link.projectId,
        runId: run.lease?.runId,
        ...turns.createdFrameFields(req as AuthenticatedUserRequest),
      });
      timer.mark('conversation_created');

      const pump = new TurnStreamPump({
        res,
        requestId,
        startTime,
        orgId,
        conversation,
        run,
        gate,
        modelInfo: extractModelInfo(body),
        agent: kind === 'agent',
        upstreamAbort,
        extension: turns.streamFor(req as AuthenticatedUserRequest, deps),
        question: turn.userRow,
        frame: streamFrameOf(req),
      });
      live.pump = pump;
      if (live.lost) {
        await pump.onLost();
        return;
      }

      const aiRequest = buildAiChatRequest(
        responder.target,
        aiRequestBody(body, attachments, run),
        {
          conversationId,
          previousConversations:
            (body.previousConversations as unknown[] | undefined) || [],
          isNewConversation: true,
          extraPayload: turns.aiPayloadFields(conversation),
          project: link.project,
          guestAgent: responder.respondingAgentKey !== undefined,
        },
      );
      if (link.projectId) {
        void ProjectService.touchActivity(link.projectId);
      }
      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}${aiRequest.path}/stream`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: { ...aiRequest.payload, protocol: AGUI_PROTOCOL },
      };

      const stream = await startAIStream(
        aiCommandOptions,
        responder.target.kind === 'agent' ? 'Agent Chat Stream' : 'Chat Stream',
        responder.target.kind === 'agent'
          ? { requestId, agentKey: responder.target.agentKey }
          : { requestId },
        upstreamAbort.signal,
      );
      if (!stream) {
        // The lost-lease or abandon path already ended the turn.
        if (upstreamAbort.isAborted()) return;
        throw new Error('Failed to get stream from AI service');
      }
      timer.mark('ai_stream_open');
      if (responder.target.kind === 'assistant') {
        stream.once('data', () => {
          timer.mark('ai_first_byte');
          timer.emit('chat stream (node)', {
            requestId,
            agentMode: parseChatMode(body.chatMode).agentMode,
          });
        });
      }
      upstreamAbort.bindStream(stream);
      keepRunAlive(stream, conversation._id, undefined, gate.lease);
      pump.attach(stream);
    } catch (error: unknown) {
      logger.error('Error in first-send stream', {
        requestId,
        agentKey,
        error: error instanceof Error ? error.message : String(error),
      });
      if (live.pump) {
        await live.pump.fail(error);
        return;
      }
      // No turn ran (or its conversation could not be reached), so the lease, if any, goes back as it was.
      await gate.settle(outcomeForError(error));
      if (extended && !res.headersSent && next) {
        next(error instanceof LeaseLostError ? new RunLostError() : error);
        return;
      }
      if (!res.headersSent) {
        res.writeHead(500, {
          'Content-Type': 'text/event-stream; charset=utf-8',
        });
      }
      res.write(
        frameAGUI(AGUIEventType.RUN_ERROR, {
          message: userFacingChatError(error),
          code: 'internal_error',
        }),
      );
      res.end();
    }
  });
