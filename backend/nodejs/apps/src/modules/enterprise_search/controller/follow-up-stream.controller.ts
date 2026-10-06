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
import { RunLostError } from '../services/collaboration/domain/errors';
import { LeaseLostError } from '../services/collaboration/leases/lease.types';
import { ConversationTurnDeps } from '../services/collaboration/turn/turn-deps';
import {
  openTurnGate,
  outcomeForError,
  TurnGate,
} from '../services/collaboration/turn/turn-gate';
import { AGUI_PROTOCOL, AGUIEventType, frameAGUI } from '../utils/agui';
import { startAIStream } from '../utils/ai-stream';
import { buildAiChatRequest, ChatTarget } from '../utils/ai-chat-payload';
import { readAclVersion } from '../../authz/cache/acl-version';
import { userFacingChatError } from '../utils/chat-error-messages';
import { filterOwnedAttachments } from '../utils/attachment-validation';
import {
  aiRequestBody,
  appendFollowUpQuery,
  followUpContext,
  FollowUpBody,
  turnCallerOf,
} from '../utils/follow-up-turn';
import { attachUpstreamAbort } from '../utils/stream-lifecycle';
import { openTurnSse, TurnStreamPump } from '../utils/turn-stream';
import { extractModelInfo } from '../utils/utils';

const logger = Logger.getInstance({ service: 'Follow-up stream' });

type TurnRequest = AuthenticatedUserRequest | AuthenticatedServiceRequest;

/**
 * The streaming follow-up turn for a chat (C3/C4) or an agent conversation (A2/A3): appends the
 * caller's message, relays the AI backend's stream, and saves the answer. With the flag on it runs
 * under the lease `runLease()` acquired; with it off the same steps run unfenced.
 */
export const followUpStream = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
  kind: ChatTarget['kind'],
) =>
  catchAsync<TurnRequest>(async (req, res, next): Promise<void> => {
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

    // The heartbeat and the client's close can fire before the pump exists, so they reach it through this holder.
    const live: { pump?: TurnStreamPump; lost: boolean } = { lost: false };
    let gate: TurnGate;
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
      if (!gate.lease) openTurnSse(res, conversationId as string);

      const attachments = await filterOwnedAttachments(
        appConfig,
        {
          userId,
          orgId,
          isServiceAccount: (req as AuthenticatedUserRequest).user
            ?.isServiceAccount as boolean | undefined,
        },
        body.attachments,
      );
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
        openTurnSse(res, conversationId as string, gate.lease.runId);

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
      });
      live.pump = pump;
      if (upstreamAbort.isClientDisconnected()) {
        await pump.onDisconnect();
        return;
      }

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
        target,
        aiRequestBody(body, attachments, turn.run),
        {
          conversationId,
          previousConversations: context.previousConversations,
          isNewConversation: false,
          aclVersion: readAclVersion(turn.conversation),
          project: context.project,
          collaboration: context.collaboration,
          mentions: context.mentions,
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

      let stream: Awaited<ReturnType<typeof startAIStream>>;
      try {
        stream = await startAIStream(
          aiCommandOptions,
          kind === 'agent' ? 'Add Message Agent Stream' : 'Add Message Stream',
          kind === 'agent' ? { requestId, agentKey } : { requestId },
          upstreamAbort.signal,
        );
      } catch (streamOpenError) {
        // The disconnect or lost-lease path already ended the turn; a second error frame would race it.
        if (upstreamAbort.isClientDisconnected()) {
          logger.debug('Client disconnected before AI stream opened', {
            requestId,
          });
          await pump.onDisconnect();
          return;
        }
        if (upstreamAbort.isAborted()) return;
        throw streamOpenError;
      }
      upstreamAbort.bindStream(stream);
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
        res.writeHead(500, { 'Content-Type': 'text/event-stream' });
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
