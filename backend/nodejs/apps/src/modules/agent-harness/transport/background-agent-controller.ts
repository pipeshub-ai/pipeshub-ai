import { NextFunction, Response } from 'express';
import { Container, interfaces } from 'inversify';
import { ServiceUnavailableError } from '../../../libs/errors/http.errors';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import type { ConversationGrantService } from '../application/conversation-grants';
import type { InboxFeedService } from '../application/inbox-feed';
import type { JobBriefService } from '../application/job-briefs';
import type { RunListService } from '../application/run-lists';
import {
  AGENT_HARNESS_CONVERSATION_GRANTS,
  AGENT_HARNESS_INBOX,
  AGENT_HARNESS_JOB_BRIEFS,
  AGENT_HARNESS_RUN_LISTS,
} from '../binders/tokens';
import { transportError } from './errors';
import { inboxViewer } from './inbox-routes';
import { viewerScope } from './stream-controller';

/**
 * UI-B1 (ADR-UI-01 D2, D3; ADR-GRANT-01 D8): a conversation's routes for the background-agents interface. The org,
 * the person and the conversation come from verified auth and the path; each service checks who may read or change
 * the conversation (owner, or a shared reader for the seen mark) before it reads anything, and answers 404 otherwise.
 */
export class BackgroundAgentController {
  constructor(
    private readonly container: Container,
    private readonly kind: 'agent' | 'chat',
  ) {}

  private service<T>(token: interfaces.ServiceIdentifier<T>): T {
    if (!this.container.isBound(token)) {
      throw new ServiceUnavailableError(
        'Background agents are not available on this deployment',
      );
    }
    return this.container.get<T>(token);
  }

  /** `GET …/runs/:runId/brief`: a job's brief (`job-brief.schema.json`), its conversation's owner only. */
  brief = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const brief = await this.service<JobBriefService>(
        AGENT_HARNESS_JOB_BRIEFS,
      ).brief(viewerScope(req, this.kind), String(req.params['runId']));
      res.status(200).json(brief);
    } catch (error) {
      next(transportError(error));
    }
  };

  /** `GET …/runs/:runId/brief/history`: how a job's goal changed (`job-brief-history.schema.json`), owner only. */
  briefHistory = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const history = await this.service<JobBriefService>(
        AGENT_HARNESS_JOB_BRIEFS,
      ).history(viewerScope(req, this.kind), String(req.params['runId']));
      res.status(200).json(history);
    } catch (error) {
      next(transportError(error));
    }
  };

  /** `GET …/runs/:runId/jobs`: one request and its jobs, "N of M back" (`request-jobs.schema.json`), owner only. */
  requestJobs = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const view = await this.service<JobBriefService>(
        AGENT_HARNESS_JOB_BRIEFS,
      ).requestJobs(viewerScope(req, this.kind), String(req.params['runId']));
      res.status(200).json(view);
    } catch (error) {
      next(transportError(error));
    }
  };

  /**
   * `PUT …/seen`: the caller opened the conversation (`conversation-seen.schema.json`). Its finished-run notices are
   * read too (ADR-INBOX-01), after the access check the mark made, so the Inbox badge and the chat list agree.
   */
  seen = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const scope = viewerScope(req, this.kind);
      const mark = await this.service<RunListService>(
        AGENT_HARNESS_RUN_LISTS,
      ).markSeen(scope);
      if (this.container.isBound(AGENT_HARNESS_INBOX)) {
        await this.container
          .get<InboxFeedService>(AGENT_HARNESS_INBOX)
          .apply(await inboxViewer(req, this.container), 'read', {
            conversationId: scope.sessionId,
          });
      }
      res.status(200).json(mark);
    } catch (error) {
      next(transportError(error));
    }
  };

  /** `GET …/grants`: what "Allow for this chat" allows here (`conversation-grants.schema.json`), owner only. */
  grants = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const grants = await this.service<ConversationGrantService<unknown>>(
        AGENT_HARNESS_CONVERSATION_GRANTS,
      ).list(viewerScope(req, this.kind));
      res.status(200).json({ grants });
    } catch (error) {
      next(transportError(error));
    }
  };

  /** `DELETE …/grants/:grantId`: the owner takes a grant back (`conversation-grant-revocation.schema.json`). */
  revokeGrant = async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const revocation = await this.service<ConversationGrantService<unknown>>(
        AGENT_HARNESS_CONVERSATION_GRANTS,
      ).revoke(viewerScope(req, this.kind), String(req.params['grantId']));
      res.status(200).json(revocation);
    } catch (error) {
      next(transportError(error));
    }
  };
}
