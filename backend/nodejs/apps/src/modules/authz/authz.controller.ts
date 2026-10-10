import { NextFunction, RequestHandler, Response } from 'express';
import { UnauthorizedError } from '../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../libs/middlewares/types';
import { callerIdentityOf } from '../../libs/types/caller-identity';
import { TeamResolutionUnavailableError } from '../enterprise_search/services/collaboration/domain/errors';
import { conversationGrantOf } from '../enterprise_search/services/collaboration/http/conversation-context';
import { AccessChange, AccessPreviewService } from './access-preview.service';
import { REDACTED_TEAM_REF, ExplainService } from './explain.service';
import { SubjectTeamResolver } from './subject-team.resolver';
import { refId } from './validators/authz.validators';

type Handler = (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => Promise<void>;

const guarded =
  (run: Handler): RequestHandler =>
  async (req, res, next): Promise<void> => {
    try {
      await run(req as AuthenticatedUserRequest, res, next);
    } catch (error) {
      next(error);
    }
  };

/** User-facing authorization routes: why a person has access, and what a change would do. */
export class AuthzController {
  constructor(
    private readonly explainer: ExplainService,
    private readonly previewer: AccessPreviewService,
    private readonly teams: SubjectTeamResolver,
  ) {}

  explain: RequestHandler = guarded(async (req, res) => {
    const identity = callerIdentityOf(req);
    if (identity.userId === '' || identity.orgId === '') {
      throw new UnauthorizedError('Authentication required');
    }
    const { resource, subject } = req.query as {
      resource: string;
      subject?: string;
    };
    const caller = {
      userId: identity.userId,
      orgId: identity.orgId,
      teamIds: await this.teams.forCaller(identity),
    };
    const explanation = await this.explainer.explainUser(
      caller,
      subject === undefined ? caller.userId : refId(subject),
      { type: 'chat', id: refId(resource) },
      (userId) => this.teams.forUser(userId, caller.orgId),
    );
    if (explanation.teamsUnresolved) {
      throw new TeamResolutionUnavailableError();
    }
    res.status(200).json({
      role: explanation.role,
      via: explanation.via.map((p) => ({
        type: p.type,
        ref: p.ref === REDACTED_TEAM_REF ? null : p.ref,
        role: p.role,
      })),
    });
  });

  preview: RequestHandler = guarded(async (req, res) => {
    const grant = conversationGrantOf(req);
    const { change } = req.body as { change: AccessChange };
    const preview = await this.previewer.preview(
      grant.caller,
      grant.session._id.toString(),
      change,
      () => this.teams.forCaller(callerIdentityOf(req)),
    );
    res.status(200).json(preview);
  });
}
