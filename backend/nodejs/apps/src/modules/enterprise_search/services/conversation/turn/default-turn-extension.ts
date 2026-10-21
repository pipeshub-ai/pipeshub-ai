import { AIServiceCommand } from '../../../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../../../libs/enums/http-methods.enum';
import { createPlainFirstTurn } from '../../../utils/first-turn';
import {
  appendPlainFollowUp,
  plainFollowUpContext,
} from '../../../utils/follow-up-turn';
import { DEFAULT_RUN_LIMITS } from '../../../utils/turn-stream';
import { formatPreviousConversations } from '../../../utils/utils';
import { TurnExtension } from './turn-extension';

/** A conversation with one author: the route guard is the only precondition and its own assistant or agent answers. */
export const defaultTurnExtension: TurnExtension = {
  screenFirstSend: () => undefined,
  prepareFirstSend: ({ target }) => Promise.resolve({ responder: { target } }),
  createFirstTurn: (input) => createPlainFirstTurn(input),
  replyWithoutRun: () => Promise.resolve(false),
  responderFor: (_req, target) => ({ target }),
  respondingAgentKeyOf: () => undefined,
  appendFollowUp: (input) => appendPlainFollowUp(input),
  followUpContext: (_req, turn, deps) => plainFollowUpContext(turn, deps),
  regenerationContext: ({ history }) =>
    Promise.resolve({
      previousConversations: formatPreviousConversations(history),
      aiPayload: {},
    }),
  createdFrameFields: () => ({}),
  aiPayloadFields: () => ({}),
  cancelRun: ({ req, appConfig, conversationId, runId }) =>
    new AIServiceCommand<unknown>({
      uri: `${appConfig.aiBackend}/api/v1/chat/cancel`,
      method: HttpMethod.POST,
      headers: {
        ...(req.headers as Record<string, string>),
        'Content-Type': 'application/json',
      },
      body: { runId, conversationId },
    }).execute(),
  toolCardFor: () => undefined,
  streamFor: () => ({
    respondingAgentView: () => Promise.resolve(undefined),
    runLimits: () => DEFAULT_RUN_LIMITS,
  }),
  queryForValidation: (query) => query,
};
