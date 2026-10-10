import { AIServiceCommand } from '../../../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../../../libs/enums/http-methods.enum';
import { TokenScopes } from '../../../../../libs/enums/token-scopes.enum';
import { IServiceTokenIssuer } from '../../../../../libs/services/service-token.issuer';
import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { AIServiceResponse } from '../../../types/conversation.interfaces';
import { decide } from '../access/conversation-access.policy';
import { errorForCode } from '../access/conversation-access.authorizer';
import { conversationGrantOf } from './conversation-context';

/** The run a cancel will stop. `starterUserId` is unknown with the flag off, which keeps the legacy call. */
export interface RunToCancel {
  readonly runId: string;
  readonly starterUserId?: string;
}

/**
 * Stops a run. The starter's own cancel is relayed with their token, as before. Anyone else the
 * policy lets cancel (`cancel` in the operation table) gets a one-minute service token bound to this
 * conversation and run, because Python's user route only lets the starter stop a run.
 */
export class RunCanceller {
  constructor(
    private readonly tokens: IServiceTokenIssuer,
    private readonly aiBackend: () => string,
  ) {}

  async cancel(
    req: AuthenticatedUserRequest,
    conversationId: string,
    run: RunToCancel,
  ): Promise<AIServiceResponse<unknown>> {
    const { role, caller } = conversationGrantOf(req);
    const ownRun =
      run.starterUserId === undefined || run.starterUserId === caller.userId;
    const body = { runId: run.runId, conversationId };
    if (ownRun) {
      return new AIServiceCommand<unknown>({
        uri: `${this.aiBackend()}/api/v1/chat/cancel`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body,
      }).execute();
    }
    const decision = decide('cancel', role);
    if (!decision.allowed) {
      throw errorForCode(decision.code);
    }
    const token = this.tokens.issue(
      {
        userId: caller.userId,
        orgId: caller.orgId,
        scopes: [TokenScopes.CONVERSATION_CANCEL],
        conversationId,
        runId: run.runId,
      },
      '1m',
    );
    return new AIServiceCommand<unknown>({
      uri: `${this.aiBackend()}/api/v1/chat/cancel/participant`,
      method: HttpMethod.POST,
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body,
    }).execute();
  }
}
