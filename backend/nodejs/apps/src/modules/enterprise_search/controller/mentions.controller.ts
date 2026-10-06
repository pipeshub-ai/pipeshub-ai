import { NextFunction, RequestHandler, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { callerIdentityOf } from '../../../libs/types/caller-identity';
import { conversationGrantOf } from '../services/collaboration/http/conversation-context';
import { IMentionablesService } from '../services/collaboration/mentions/mentionables.service';
import {
  NoteInput,
  INoteService,
} from '../services/collaboration/mentions/note.service';
import { SessionKind } from '../services/collaboration/mentions/mention.types';

/** The mentionables and notes routes; the kind says which router mounted it. */
export class MentionsController {
  constructor(
    private readonly notes: INoteService,
    private readonly mentionables: IMentionablesService,
  ) {}

  list: RequestHandler = this.handle(async (req, res) => {
    const { q, limit } = req.query as unknown as { q: string; limit: number };
    res
      .status(200)
      .json(
        await this.mentionables.list(
          conversationGrantOf(req),
          callerIdentityOf(req),
          { q, limit },
        ),
      );
  });

  /** The picker for a chat that is not created yet; the caller is its would-be owner. */
  listForNewChat: RequestHandler = this.handle(async (req, res) => {
    const { q, limit, include } = req.query as unknown as {
      q: string;
      limit: number;
      include: string[];
    };
    res.status(200).json(
      await this.mentionables.listForNewChat(callerIdentityOf(req), {
        q,
        limit,
        include,
        ...(req.params.agentKey !== undefined && {
          agentKey: req.params.agentKey,
        }),
      }),
    );
  });

  postNote = (kind: SessionKind): RequestHandler =>
    this.handle(async (req, res) => {
      const outcome = await this.notes.post(
        conversationGrantOf(req),
        callerIdentityOf(req),
        kind,
        req.body as NoteInput,
      );
      res.status(outcome.duplicate ? 200 : 201).json(outcome);
    });

  private handle(
    run: (req: AuthenticatedUserRequest, res: Response) => Promise<void>,
  ): RequestHandler {
    return (req, res, next: NextFunction): void => {
      run(req as AuthenticatedUserRequest, res).catch(next);
    };
  }
}
