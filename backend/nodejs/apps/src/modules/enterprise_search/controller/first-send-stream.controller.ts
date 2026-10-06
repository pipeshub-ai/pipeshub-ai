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
import { collabEnabledFor } from '../services/collaboration/http/conversation-context';
import { LeaseLostError } from '../services/collaboration/leases/lease.types';
import { ConversationTurnDeps } from '../services/collaboration/turn/turn-deps';
import {
  holdLease,
  outcomeForError,
  TurnGate,
  unleasedGate,
} from '../services/collaboration/turn/turn-gate';
import { AGUI_PROTOCOL, AGUIEventType, frameAGUI } from '../utils/agui';
import { startAIStream } from '../utils/ai-stream';
import { readAclVersion } from '../../authz/cache/acl-version';
import {
  buildAiChatRequest,
  ChatTarget,
  parseChatMode,
} from '../utils/ai-chat-payload';
import { userFacingChatError } from '../utils/chat-error-messages';
import { filterOwnedAttachments } from '../utils/attachment-validation';
import {
  createFirstTurn,
  rejectRepeatedFirstSend,
  rejectResumeOnFirstSend,
} from '../utils/first-turn';
import {
  aiRequestBody,
  FollowUpBody,
  turnCallerOf,
} from '../utils/follow-up-turn';
import { resolveProjectLink } from '../utils/project-context';
import { attachUpstreamAbort } from '../utils/stream-lifecycle';
import {
  TurnStreamPump,
  writeConversationCreated,
  writeSseHead,
} from '../utils/turn-stream';
import { extractModelInfo, StageTimer } from '../utils/utils';

const logger = Logger.getInstance({ service: 'First-send stream' });

type TurnRequest = AuthenticatedUserRequest | AuthenticatedServiceRequest;

/**
 * The streaming first send for a chat (`/stream`) or an agent (`/:agentKey/conversations/stream`):
 * creates the conversation with the caller's first message, relays the AI backend's stream, and
 * saves the answer. With the flag on, a repeated `clientMessageId` is rejected and the session is
 * born holding the run's lease, both before any SSE header; with it off the stream opens first and
 * nothing is keyed or leased.
 */
export const firstSendStream = (
  appConfig: AppConfig,
  deps: ConversationTurnDeps,
  kind: ChatTarget['kind'],
) =>
  catchAsync<TurnRequest>(async (req, res, next): Promise<void> => {
    const requestId = req.context?.requestId;
    const startTime = Date.now();
    const timer = new StageTimer();
    const body = req.body as FollowUpBody;
    if (!body.query) {
      throw new BadRequestError('Query is required');
    }
    const { agentKey } = req.params;
    const { userId, orgId } = turnCallerOf(req);
    const collab = collabEnabledFor(req);
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
      if (!collab) writeSseHead(res);
      rejectResumeOnFirstSend(collab, body.resume);
      await rejectRepeatedFirstSend(
        collab,
        orgId,
        userId,
        body.clientMessageId,
      );

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
      const link = await resolveProjectLink(
        String(orgId),
        String(userId),
        body,
      );
      const turn = await createFirstTurn({
        deps,
        collab,
        target,
        userId,
        orgId,
        body,
        attachments,
        link,
      });
      const { conversation, run } = turn;
      gate = run.lease
        ? holdLease(run.lease, () => {
            live.lost = true;
            void live.pump?.onLost();
          })
        : unleasedGate();
      const conversationId = String(conversation._id);
      logger.debug('Initial conversation created', {
        requestId,
        conversationId,
        userId,
        agentKey,
      });

      // With a lease every 4xx so far was JSON; from here on failures are SSE frames.
      if (collab) writeSseHead(res, run.lease?.runId);
      writeConversationCreated(res, {
        conversationId,
        title: conversation.title || undefined,
        projectId: link.projectId,
        runId: run.lease?.runId,
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
      });
      live.pump = pump;
      if (live.lost) {
        await pump.onLost();
        return;
      }
      if (upstreamAbort.isClientDisconnected()) {
        await pump.onDisconnect();
        return;
      }

      const aiRequest = buildAiChatRequest(
        target,
        aiRequestBody(body, attachments, run),
        {
          conversationId,
          previousConversations:
            (body.previousConversations as unknown[] | undefined) || [],
          isNewConversation: true,
          aclVersion: readAclVersion(conversation),
          project: link.project,
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

      let stream: Awaited<ReturnType<typeof startAIStream>>;
      try {
        stream = await startAIStream(
          aiCommandOptions,
          kind === 'agent' ? 'Agent Chat Stream' : 'Chat Stream',
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
      timer.mark('ai_stream_open');
      if (kind === 'assistant') {
        stream.once('data', () => {
          timer.mark('ai_first_byte');
          timer.emit('chat stream (node)', {
            requestId,
            agentMode: parseChatMode(body.chatMode).agentMode,
          });
        });
      }
      upstreamAbort.bindStream(stream);
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
      if (collab && !res.headersSent && next) {
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
