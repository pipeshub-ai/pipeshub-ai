import { NextFunction, Response } from 'express';
import { z } from 'zod';
import {
  BadRequestError,
  ForbiddenError,
  GoneError,
  HttpError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { getUserRepository } from '../../../libs/repositories/user';
import { resolveSoleOrgId } from '../../enterprise_search/utils/scoped-request';
import { getSlackBotStore } from '../controller/cm_controller';
import {
  SlackIdentityLinks,
  SlackLinkRequestError,
  isSlackId,
} from './slack-identity-links';
import {
  LinkedAccount,
  LinkedAccountUsers,
  accountOf,
  liveLinkedAccount,
} from './slack-linked-account';

/** Where a person approves a link; also in Node's unframeable pages. */
export const SLACK_LINK_PAGE_PATH = '/slack/link';

const slackId = z.string().refine(isSlackId, { message: 'Not a Slack id' });

export const slackCallerSchema = z.object({
  body: z.object({
    /** The organisation the bot serves; this edition serves the install's only one. */
    orgId: z.string().min(1).max(64),
    configId: z.string().min(1).max(200),
    teamId: slackId,
    slackUserId: slackId,
    slackName: z.string().max(200).optional(),
    /** false: the bot told this person how to link moments ago, so the link it sent must stay the live one. */
    issueLink: z.boolean().optional(),
  }),
});

export const slackLinkTokenSchema = z.object({
  body: z.object({ token: z.string().min(1).max(200) }),
});

export const slackLinkDecisionSchema = z.object({
  body: z.object({
    token: z.string().min(1).max(200),
    action: z.enum(['approve', 'deny']),
  }),
});

export const slackLinkRevokeSchema = z.object({
  params: z.object({ teamId: slackId, slackUserId: slackId }),
});

/** The page translates the other-organisation refusal by its code (`HTTP_SLACK_LINK_OTHER_ORG`). */
export const SLACK_LINK_OTHER_ORG_CODE = 'SLACK_LINK_OTHER_ORG';

function refusal(error: unknown): unknown {
  if (!(error instanceof SlackLinkRequestError)) return error;
  if (error.problem === 'other_org')
    return new HttpError(SLACK_LINK_OTHER_ORG_CODE, error.message, 403);
  return error.problem === 'unknown'
    ? new BadRequestError(error.message)
    : new GoneError(error.message);
}

/**
 * Which organisation each handler acts for (ADR-SLACKID-01 D7). A deployment that serves several organisations passes
 * its own; the default is this edition's: the install's only organisation, and a bot from the stored Slack bot list.
 */
export interface SlackIdentityOrgs {
  /** The bot's organisation and name, or null when no bot has that configuration id. */
  botOf(configId: string): Promise<{ orgId: string; name?: string } | null>;
  /** The organisation a person signed in to the link page belongs to. */
  personOrg(req: AuthenticatedUserRequest): Promise<string>;
  /** The organisation an admin listing or removing links acts for. */
  adminOrg(req: AuthenticatedUserRequest): Promise<string>;
}

export function soleOrgSlackIdentityOrgs(
  kv?: KeyValueStoreService,
): SlackIdentityOrgs {
  return {
    botOf: async (configId) => {
      if (!kv) return null;
      const bot = (await getSlackBotStore(kv)).configs.find(
        (config) => config.id === configId,
      );
      return bot ? { orgId: await resolveSoleOrgId(), name: bot.name } : null;
    },
    personOrg: () => resolveSoleOrgId(),
    adminOrg: () => resolveSoleOrgId(),
  };
}

/** A person signed in to the organisation the page acts for, as themselves: a link binds a person, never an
 * integration's or a service account's token. */
async function linkingPerson(
  req: AuthenticatedUserRequest,
  users: LinkedAccountUsers,
  orgs: SlackIdentityOrgs,
): Promise<LinkedAccount> {
  const { orgId, userId } = req.user ?? {};
  if (!orgId || !userId || req.user?.isServiceAccount) {
    throw new ForbiddenError(
      'Sign in to PipesHub as yourself to link your Slack account.',
    );
  }
  const account = await accountOf(
    {
      orgId: String(orgId),
      userId: String(userId),
      teamId: '',
      slackUserId: '',
      linkedAt: '',
    },
    await orgs.personOrg(req),
    users,
  );
  if (!account) {
    throw new ForbiddenError(
      'This PipesHub account cannot be linked to Slack.',
    );
  }
  return account;
}

/**
 * The bot asks who a Slack person is. A linked, live account comes back as its PipesHub address; otherwise a
 * fresh single-use sign-in link for the bot to send them privately.
 */
export const resolveSlackCaller =
  (
    kv: KeyValueStoreService,
    links: SlackIdentityLinks,
    frontendUrl: () => string,
    users?: LinkedAccountUsers,
    orgs: SlackIdentityOrgs = soleOrgSlackIdentityOrgs(kv),
  ) =>
  async (
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const {
        orgId: askedOrg,
        configId,
        teamId,
        slackUserId,
        slackName,
        issueLink,
      } = req.body as z.infer<typeof slackCallerSchema>['body'];
      const bot = await orgs.botOf(configId);
      if (!bot) throw new NotFoundError('Slack Bot configuration not found');
      const orgId = bot.orgId;
      // The bot names the organisation it serves; an answer from another organisation's links is never given.
      if (askedOrg !== orgId)
        throw new ForbiddenError(
          'This Slack bot does not serve that organization',
        );
      const account = await liveLinkedAccount(
        links,
        { teamId, slackUserId },
        orgId,
        users ?? getUserRepository(),
      );
      if (account) {
        res.status(200).json({
          linked: true,
          email: account.email,
          fullName: account.fullName ?? null,
        });
        return;
      }
      if (issueLink === false) {
        res.status(200).json({ linked: false, linkUrl: null });
        return;
      }
      const { token, expiresAt } = await links.request({
        orgId,
        configId,
        teamId,
        slackUserId,
        slackName,
        botName: bot.name,
      });
      const base = frontendUrl().replace(/\/+$/, '');
      res.status(200).json({
        linked: false,
        linkUrl: `${base}${SLACK_LINK_PAGE_PATH}?token=${encodeURIComponent(token)}`,
        expiresAt: new Date(expiresAt).toISOString(),
      });
    } catch (error) {
      next(error);
    }
  };

export const inspectSlackLinkRequest =
  (
    links: SlackIdentityLinks,
    users?: LinkedAccountUsers,
    orgs: SlackIdentityOrgs = soleOrgSlackIdentityOrgs(),
  ) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const person = await linkingPerson(
        req,
        users ?? getUserRepository(),
        orgs,
      );
      const request = await links.inspect(
        (req.body as { token: string }).token,
        person.orgId,
      );
      const existing = await links.find(person.orgId, request);
      res.status(200).json({
        slack: {
          teamId: request.teamId,
          userId: request.slackUserId,
          name: request.slackName ?? null,
        },
        botName: request.botName ?? null,
        expiresAt: new Date(request.expiresAt).toISOString(),
        account: { email: person.email, fullName: person.fullName ?? null },
        alreadyLinkedToYou: existing?.userId === person.userId,
        linkedToAnotherAccount: Boolean(
          existing && existing.userId !== person.userId,
        ),
      });
    } catch (error) {
      next(refusal(error));
    }
  };

export const decideSlackLinkRequest =
  (
    links: SlackIdentityLinks,
    users?: LinkedAccountUsers,
    orgs: SlackIdentityOrgs = soleOrgSlackIdentityOrgs(),
  ) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const person = await linkingPerson(
        req,
        users ?? getUserRepository(),
        orgs,
      );
      const { token, action } = req.body as z.infer<
        typeof slackLinkDecisionSchema
      >['body'];
      if (action === 'deny') {
        await links.decline(token, person.orgId);
        res.status(200).json({ linked: false });
        return;
      }
      const link = await links.complete(token, {
        orgId: person.orgId,
        userId: person.userId,
        email: person.email,
      });
      res.status(200).json({
        linked: true,
        slack: { teamId: link.teamId, userId: link.slackUserId },
      });
    } catch (error) {
      next(refusal(error));
    }
  };

/** Admin view: every linked Slack person and the account they act as; a link whose account is gone shows inactive. */
export const listSlackIdentityLinks =
  (
    links: SlackIdentityLinks,
    users?: LinkedAccountUsers,
    orgs: SlackIdentityOrgs = soleOrgSlackIdentityOrgs(),
  ) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const orgId = await orgs.adminOrg(req);
      const rows = [];
      for (const link of await links.list(orgId)) {
        const account = await accountOf(
          link,
          orgId,
          users ?? getUserRepository(),
        );
        rows.push({
          teamId: link.teamId,
          slackUserId: link.slackUserId,
          slackName: link.slackName ?? null,
          userId: link.userId,
          email: account?.email ?? null,
          fullName: account?.fullName ?? null,
          linkedAt: link.linkedAt,
          active: account !== null,
        });
      }
      res.status(200).json({ links: rows });
    } catch (error) {
      next(error);
    }
  };

export const revokeSlackIdentityLink =
  (
    links: SlackIdentityLinks,
    orgs: SlackIdentityOrgs = soleOrgSlackIdentityOrgs(),
  ) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { teamId, slackUserId } = req.params as {
        teamId: string;
        slackUserId: string;
      };
      const adminId = req.user?.userId as string | undefined;
      if (!adminId)
        throw new ForbiddenError('Sign in as an admin to remove a Slack link.');
      if (
        !(await links.revoke(
          await orgs.adminOrg(req),
          { teamId, slackUserId },
          { by: 'admin', adminId },
        ))
      ) {
        throw new NotFoundError('No linked Slack account with that id');
      }
      res.status(200).json({ revoked: true });
    } catch (error) {
      next(error);
    }
  };
