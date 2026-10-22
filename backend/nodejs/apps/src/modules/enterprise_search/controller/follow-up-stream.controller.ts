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
import { conversationExtensions } from '../services/conversation/conversation-extensions';
import { LeaseLostError } from '../services/conversation/turn/run-lease';
import { ConversationTurnDeps } from '../services/conversation/turn/turn-deps';
import {
  openTurnGate,
  outcomeForError,
  reservedGate,
  TurnGate,
} from '../services/conversation/turn/turn-gate';
import { EXCLUDE_AGENT, ONLY_AGENT } from '../constants/constants';
import { AGUI_PROTOCOL, AGUIEventType, frameAGUI } from '../utils/agui';
import { startAIStream } from '../utils/ai-stream';
import { buildAiChatRequest, ChatTarget } from '../utils/ai-chat-payload';
import { userFacingChatError } from '../utils/chat-error-messages';
import {
  aiRequestBody,
  appendFollowUpQuery,
  followUpContext,
  FollowUpBody,
  turnCallerOf,
} from '../utils/follow-up-turn';
import { refuseRunWhileShuttingDown } from '../utils/active-runs';
import { attachUpstreamAbort } from '../utils/stream-lifecycle';
import { openTurnSse, TurnStreamPump } from '../utils/turn-stream';
import {
  extractModelInfo,
  keepRunAlive,
  reserveRun,
  RunReservation,
  streamFrameOf,
} from '../utils/utils';

const logger = Logger.getInstance({ service: 'Follow-up stream' });

type TurnRequest = AuthenticatedUserRequest | AuthenticatedServiceRequest;

/**
 * The streaming follow-up turn for a chat or an agent conversation: appends the
 * caller's message, relays the AI backend's stream, and saves the answer. Under an extended guard
 * policy it runs under the lease `runLease()` acquired; otherwise the same steps run unfenced.
 */
export const followUpStream = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
  kind: ChatTarget['kind'],
): ((req: TurnRequest, res: Response, next?: NextFunction) => Promise<void>) =>
  catchAsync<TurnRequest>(async (req, res, next): Promise<void> => {
    refuseRunWhileShuttingDown();
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const { conversationId, agentKey } = req.params;
    const body = req.body as FollowUpBody;
    if (!body.query) {
      throw new BadRequestError('Query is required');
    }
    const { userId, orgId } = turnCallerOf(req);
    const target: ChatTarget =
      kind === 'agent'
        ? { kind: 'agent', agentKey: agentKey as string }
        : { kind: 'assistant' };

    const turns = conversationExtensions().turns;
    const responder = turns.responderFor(
      req as AuthenticatedUserRequest,
      target,
    );

    // The heartbeat and the client's close can fire before the pump exists, so they reach it through this holder.
    const live: { pump?: TurnStreamPump; lost: boolean } = { lost: false };
    let gate: TurnGate;
    // Without a lease the thread is claimed by `reserveRun` instead.
    let reservation: RunReservation | null = null;
    try {
      gate = openTurnGate(req as AuthenticatedUserRequest, () => {
        live.lost = true;
        void live.pump?.onLost();
      });
    } catch (error) {
      if (error instanceof LeaseLostError) return;
      throw error;
    }
    const upstreamAbort = attachUpstreamAbort(res, requestId, () => {
      void live.pump?.onDisconnect();
    });

    try {
      // Without a lease nothing can reject the turn before streaming, so the stream opens first as it always did.
      if (!gate.lease) {
        openTurnSse(res, conversationId as string);
        reservation = await reserveRun({
          _id: conversationId,
          orgId,
          userId,
          isDeleted: false,
          ...(kind === 'agent' ? { agentKey, ...ONLY_AGENT } : EXCLUDE_AGENT),
        });
        gate = reservedGate(reservation);
      }

      const { attachments } = body;
      const turn = await appendFollowUpQuery({
        req: req as AuthenticatedUserRequest,
        target,
        userId,
        orgId,
        body,
        attachments,
        gate,
      });
      if (live.lost) throw new LeaseLostError(gate.lease?.runId ?? '');
      // With a lease every 4xx so far was JSON; from here on failures are SSE frames.
      if (gate.lease)
        openTurnSse(
          res,
          conversationId as string,
          gate.lease.runId,
          turns.createdFrameFields(req as AuthenticatedUserRequest),
        );

      const pump = new TurnStreamPump({
        res,
        requestId,
        startTime,
        orgId,
        conversation: turn.conversation,
        run: turn.run,
        gate,
        modelInfo: extractModelInfo(body),
        agent: kind === 'agent',
        upstreamAbort,
        extension: turns.streamFor(req as AuthenticatedUserRequest, deps),
        question: turn.userRow,
        frame: streamFrameOf(req),
      });
      live.pump = pump;

      logger.debug('User message added to conversation', {
        requestId,
        conversationId,
        agentKey,
        userId,
      });
      const context = await followUpContext(
        req as AuthenticatedUserRequest,
        turn,
        deps,
      );
      const aiRequest = buildAiChatRequest(
        responder.target,
        aiRequestBody(body, attachments, turn.run),
        {
          conversationId,
          previousConversations: context.previousConversations,
          isNewConversation: false,
          extraPayload: {
            ...turns.aiPayloadFields(turn.conversation),
            ...context.aiPayload,
          },
          project: context.project,
          guestAgent: responder.respondingAgentKey !== undefined,
        },
      );
      if (turn.conversation.projectId) {
        void ProjectService.touchActivity(
          turn.conversation.projectId.toString(),
        );
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
        responder.target.kind === 'agent'
          ? 'Add Message Agent Stream'
          : 'Add Message Stream',
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
      upstreamAbort.bindStream(stream);
      keepRunAlive(
        stream,
        turn.conversation._id,
        reservation?.runId,
        gate.lease,
      );
      pump.attach(stream);
    } catch (error: unknown) {
      logger.error('Error in follow-up stream', {
        requestId,
        conversationId,
        agentKey,
        error: error instanceof Error ? error.message : String(error),
      });
      if (live.pump) {
        await live.pump.fail(error);
        return;
      }
      // Nothing was appended, so the session goes back to idle rather than failed.
      const outcome = outcomeForError(error);
      await gate.settle(outcome === 'failed' ? 'unstarted' : outcome);
      if (gate.lease && !res.headersSent && next) {
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
