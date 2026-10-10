import { NextFunction, RequestHandler, Response } from 'express';
import { inject, injectable } from 'inversify';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { callerIdentityOf } from '../../../libs/types/caller-identity';
import { recordFeedPoll } from '../../../libs/services/telemetry/modules/feed-poll-metrics';
import { principalSchema } from '../../../libs/validators/zod-primitives';
import { COLLAB_TYPES } from '../services/collaboration/collab.types';
import { IConversationCollaborationService } from '../services/collaboration/conversation-collaboration.service';
import {
  ConversationSettings,
  Principal,
} from '../services/collaboration/domain/types';
import { IConversationFeedService } from '../services/collaboration/feed/conversation-feed.service';
import { conversationGrantOf } from '../services/collaboration/http/conversation-context';
import { IConversationReadinessService } from '../services/collaboration/readiness/conversation-readiness.service';

export const ACL_VERSION_HEADER = 'X-Acl-Version';

type Handler = (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => Promise<void>;

interface PutBody {
  collaborators: Array<{
    principalType: 'user' | 'team';
    principalId: string;
    accessLevel: 'read' | 'write';
  }>;
  note?: string;
  confirmOrgWide?: true;
}

const toPrincipal = (
  principalType: 'user' | 'team',
  principalId: string,
): Principal =>
  principalType === 'user'
    ? { type: 'user', userId: principalId }
    : { type: 'team', teamId: principalId };

/** One class for both conversation kinds; the route table decides which guard runs first. */
@injectable()
export class CollaboratorsController {
  constructor(
    @inject(COLLAB_TYPES.CollaborationService)
    private readonly service: IConversationCollaborationService,
    @inject(COLLAB_TYPES.FeedService)
    private readonly feed: IConversationFeedService,
    @inject(COLLAB_TYPES.ReadinessService)
    private readonly readiness: IConversationReadinessService,
  ) {}

  list: RequestHandler = this.handle(async (req, res) => {
    res
      .status(200)
      .json(
        await this.service.list(
          conversationGrantOf(req),
          callerIdentityOf(req),
        ),
      );
  });

  upsert: RequestHandler = this.handle(async (req, res) => {
    const body = req.body as PutBody;
    const view = await this.service.upsert(
      conversationGrantOf(req),
      callerIdentityOf(req),
      {
        collaborators: body.collaborators.map((c) => ({
          principal: toPrincipal(c.principalType, c.principalId),
          accessLevel: c.accessLevel,
        })),
        note: body.note,
        confirmOrgWide: body.confirmOrgWide,
      },
    );
    res.status(200).json(view);
  });

  remove: RequestHandler = this.handle(async (req, res) => {
    const { principalId } = req.params as { principalId: string };
    const { principalType } = req.query as { principalType: 'user' | 'team' };
    if (!principalSchema.safeParse({ principalType, principalId }).success) {
      throw new BadRequestError(`Invalid ${principalType} ID format`);
    }
    const view = await this.service.remove(
      conversationGrantOf(req),
      callerIdentityOf(req),
      toPrincipal(principalType, principalId),
    );
    res.status(200).json(view);
  });

  updateSettings: RequestHandler = this.handle(async (req, res) => {
    const view = await this.service.updateSettings(
      conversationGrantOf(req),
      callerIdentityOf(req),
      req.body as ConversationSettings,
    );
    res.status(200).json(view);
  });

  transferOwnership: RequestHandler = this.handle(async (req, res) => {
    const { newOwnerUserId } = req.body as { newOwnerUserId: string };
    const view = await this.service.transferOwnership(
      conversationGrantOf(req),
      callerIdentityOf(req),
      newOwnerUserId,
    );
    res.status(200).json(view);
  });

  leave: RequestHandler = this.handle(async (req, res) => {
    const grant = conversationGrantOf(req);
    await this.service.leave(grant, callerIdentityOf(req));
    res.status(200).json({ id: grant.session._id, status: 'left' });
  });

  getFeed: RequestHandler = this.handle(async (req, res) => {
    const { afterSeq, rev } = req.query as unknown as {
      afterSeq: number;
      rev?: number;
    };
    const outcome = await this.feed.read(
      conversationGrantOf(req),
      { afterSeq, rev },
      callerIdentityOf(req),
    );
    // On the 304 too: a sharing change does not move `rev`, and must not hide behind it.
    res.setHeader(
      ACL_VERSION_HEADER,
      String(
        outcome.status === 'ok' ? outcome.body.aclVersion : outcome.aclVersion,
      ),
    );
    if (outcome.status === 'not_modified') {
      recordFeedPoll('304');
      res.status(304).end();
      return;
    }
    recordFeedPoll('200');
    res.status(200).json(outcome.body);
  });

  getReadiness: RequestHandler = this.handle(async (req, res) => {
    res
      .status(200)
      .json(
        await this.readiness.evaluate(
          conversationGrantOf(req),
          callerIdentityOf(req),
        ),
      );
  });

  private handle(run: Handler): RequestHandler {
    return async (req, res, next): Promise<void> => {
      try {
        await run(req as AuthenticatedUserRequest, res, next);
      } catch (error) {
        next(error);
      }
    };
  }
}
