import { Response, NextFunction } from 'express';
import { isSlackOnlyAccount } from '../constants/service-account.constants';
import { upgradeSlackOnlyAccount } from '../services/slack-only-accounts.service';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import { isValidId } from '../../../libs/repositories/ids';
import { UniqueConstraintError } from '../../../libs/repositories/errors';
import { getUserRepository } from '../../../libs/repositories/user';
import type { UserDto, DirectoryQuery } from '../../../libs/repositories/user';
import { getGroupRepository } from '../../../libs/repositories/group';
import { getCredentialRepository } from '../../../libs/repositories/credential';
import { getActivityRepository } from '../../../libs/repositories/activity';
import { getPictureRepository } from '../../../libs/repositories/picture';
import {
  assertRasterImage,
  readImageUpload,
  sharp,
} from '../../../libs/utils/image.utils';
import {
  fetchConfigJwtGenerator,
  jwtGeneratorForNewAccountPassword,
  jwtGeneratorForValidateEmailLink,
  mailJwtGenerator,
} from '../../../libs/utils/createJwt';
import { getBrandConfig } from '../../../libs/branding';
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  InternalServerError,
  LargePayloadError,
  NotFoundError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import {
  markClientSafe,
} from '../../../libs/errors/reader-friendly';
import { inject, injectable } from 'inversify';
import { MailService, mailFailureError } from '../services/mail.service';
import {
  EntitiesEventProducer,
  Event,
  EventType,
  UserAddedEvent,
  UserDeletedEvent,
  UserUpdatedEvent,
  SyncAction,
} from '../services/entity_events.service';
import { Logger } from '../../../libs/services/logger.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { isServiceAccountEmail } from '../constants/service-account.constants';
import type {
  GraphUserListResponse,
  UserGroupSummary,
} from '../types/user_management.types';
import {
  isUserOrgAdmin,
  toDisplayUserRole,
  normalizeUserRole,
  resolveOptionalUserRole,
  saveUserEnsuringOrgRetainsAdmin,
  saveUserEnsuringAdminCap,
  assertCanPromoteAdmin,
} from '../services/user-admin.service';
import { safeParsePagination } from '../../../utils/safe-integer';
import { buildPaginationMetadata } from '../../enterprise_search/utils/utils';
import { AuthService } from '../services/auth.service';
import { getOrgRepository } from '../../../libs/repositories/org';
import { passwordValidator } from '../../auth/utils/passwordValidator';
import { SALT_ROUNDS } from '../../auth/controller/userAccount.controller';
import bcrypt from 'bcryptjs';
import {
  DEMO_ACCOUNT_DOMAIN,
  clearRemovedSampleAccount,
  isDemoAccountEmail,
} from '../services/demo-accounts.service';

export {
  DEMO_ACCOUNT_DOMAIN,
  isDemoAccountEmail,
} from '../services/demo-accounts.service';
import { userActivitiesType } from '../../../libs/utils/userActivities.utils';
import { AICommandOptions } from '../../../libs/commands/ai_service/ai.service.command';
import { AIServiceCommand } from '../../../libs/commands/ai_service/ai.service.command';
import * as XLSX from 'xlsx';
import { FileBufferInfo } from '../../../libs/middlewares/file_processor/fp.interface';
import {
  NotificationProducer,
  EventType as NotificationEventType,
} from '../../notification/service/notification.producer';
import { NotificationContainer } from '../../notification/container/notification.container';
import type { NotificationEventPayload } from '../../notification/service/notification.producer';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import {
  validateNoFormatSpecifiers,
  validateNoXSS,
} from '../../../utils/xss-sanitization';
import { inInstallOrg } from '../../../libs/repositories/identity/install-org';
import { getOAuthAppRepository } from '../../../libs/repositories/oauth-app';
import { resolveOAuthTokenService } from '../../../libs/services/oauth-token-service.provider';
import { ProjectService } from '../../projects/services/project.service';
import { ProjectKnowledgeBaseService } from '../../projects/services/project-kb.service';

// What the auth service reads from an email lookup: the session and refresh
// tokens it issues, the sign-in checks, and the first-login update.
export const USER_LOOKUP_FIELDS =
  '_id orgId email fullName role hasLoggedIn kind';

export const USER_LOOKUP_EMAIL_MISMATCH_MESSAGE =
  'This lookup token was issued for a different email address.';

/**
 * Only the account's owner may change its email address.
 *
 * Connector permissions attach to the address, so an admin who could move a
 * colleague's account to an address they control could reset its password,
 * sign in, and read everything the colleague is allowed to see — then move
 * it back. Verifying the new address does not help, because the admin
 * chooses it. An invitation sent to the wrong address is fixed by deleting
 * it and inviting again, which never carries a credential.
 */
function assertEmailChangeIsSelf(
  actorUserId: unknown,
  targetUserId: unknown,
): void {
  const actor = typeof actorUserId === 'string' ? actorUserId : '';
  const target = typeof targetUserId === 'string' ? targetUserId : '';
  const isSelf =
    actor !== '' &&
    target !== '' &&
    isValidId(actor) &&
    actor.toLowerCase() === target.toLowerCase();
  if (!isSelf) {
    throw new ForbiddenError(
      'Only the account owner can change its email address. To fix an invitation sent to the wrong address, delete it and invite again.',
    );
  }
}

// Addresses are stored lowercased; compare the way they're stored.
function normalizedEmail(value: unknown): string {
  return typeof value === 'string' ? value.toLowerCase().trim() : '';
}

export const MAX_BULK_INVITE = 1000;
export const USER_DELETE_NOT_RECORDED =
  "We couldn't delete this user just now. They are still a member; please try again in a moment.";

// Linear-time email check: each segment excludes its following separator
// (`@`/`.`), so there is no ambiguous backtracking (avoids ReDoS). The local
// part also refuses quoting and list punctuation, so a spreadsheet formula such
// as `=HYPERLINK("http://x","a@b.co")` split at its comma is not an address.
const EMAIL_REGEX =
  /^[^\s@"(),:;<>[\]\\`]+@(?:[\p{L}\p{N}-]+\.)+[\p{L}\p{N}-]{2,}$/u;
// RFC 5321 caps a forward path at 254 characters.
const MAX_EMAIL_LENGTH = 254;

export function isValidEmail(email: unknown): boolean {
  return (
    typeof email === 'string' &&
    email.length <= MAX_EMAIL_LENGTH &&
    EMAIL_REGEX.test(email)
  );
}

export interface InviteResult {
  invited: number;
  restored: number;
  reinvited: number;
  alreadyActive: number;
  mailFailed: string[];
  mailErrorCode?: number;
  limitExceededRestorations?: string[];
}

// The store names the violated key; only the email key is a duplicate address.
// A caught value can be anything, null or undefined included.
function isDuplicateEmailKeyError(error: unknown): boolean {
  return (
    error instanceof UniqueConstraintError &&
    String(error.metadata?.key ?? '')
      .split(',')
      .includes('email')
  );
}

@injectable()
export class UserController {
  constructor(
    @inject('AppConfig') protected config: AppConfig,
    @inject('MailService') protected mailService: MailService,
    @inject('AuthService') protected authService: AuthService,
    @inject('Logger') protected logger: Logger,
    @inject('EntitiesEventProducer')
    protected eventService: EntitiesEventProducer,
    @inject('NotificationProducer')
    protected notificationProducer: NotificationProducer,
  ) {}

  async getAllUsers(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const {
      page: pageParam,
      limit: limitParam,
      search,
      hasLoggedIn,
      isBlocked,
      groupIds,
      includeServiceAccounts,
      includeSlackOnlyAccounts,
    } = req.query;

    const orgId = req.user?.orgId as string;
    const { page, limit, skip } = safeParsePagination(
      (pageParam as string) ?? '1',
      limitParam as string,
      1,
      25,
      100,
    );

    // This is the list of people. Service accounts are users in every way the
    // permission graph cares about, but they are managed in their own admin
    // screen, and listing them here by default has consequences beyond the
    // cosmetic: they can never log in, so they would sit in the pending-invite
    // set forever and be swept into bulk invite actions aimed at colleagues
    // who have not signed in yet.
    //
    // The screens that choose who belongs to a group or a team are the
    // exception, because membership is how a service account is given anything
    // to read, and the create panel tells an administrator to grant access
    // that way. They ask for them explicitly, so no other caller changes
    // behaviour. Each returned record carries its `kind`, which is what lets
    // those screens mark a machine identity rather than let it pass for a
    // colleague.
    //
    // Asking is not enough on its own. This route is authenticated but not
    // admin-only, and `requireScopes` does nothing for a session token, so
    // without the check below any signed-in colleague could list every service
    // account in the organisation and the groups it belongs to. The screen
    // that lists them already requires an administrator, and so does putting
    // one in a group, so honouring the flag for anyone else would hand out
    // through this route what the other one refuses.
    // Compared rather than stringified: a query value can arrive as an array
    // or an object, and only the exact string opts in.
    const wantsServiceAccounts = includeServiceAccounts === 'true';
    const maySeeServiceAccounts =
      wantsServiceAccounts &&
      req.user?.userId !== undefined &&
      (await isUserOrgAdmin(req.user.userId as string, orgId));

    // Slack-only accounts are not colleagues anyone can share with or pick,
    // so they are left out too unless an admin's users screen asks for them.
    const maySeeSlackOnlyAccounts =
      includeSlackOnlyAccounts === 'true' &&
      req.user?.userId !== undefined &&
      (maySeeServiceAccounts ||
        (await isUserOrgAdmin(req.user.userId as string, orgId)));
    const query: DirectoryQuery = {
      skip,
      limit,
      includeServiceAccounts: maySeeServiceAccounts,
      includeSlackOnlyAccounts: maySeeSlackOnlyAccounts,
    };

    if (search) {
      query.search = String(search);
    }

    // Status filters: hasLoggedIn and isBlocked
    // When both are present, either condition matches.
    if (hasLoggedIn !== undefined && hasLoggedIn !== '') {
      query.hasLoggedIn = String(hasLoggedIn) === 'true';
    }
    if (isBlocked !== undefined && isBlocked !== '') {
      query.blocked = String(isBlocked) === 'true';
    }

    // groupIds filter: restrict to users belonging to any of the specified groups
    if (groupIds) {
      const gids = String(groupIds).split(',').filter(Boolean);
      if (gids.length > 0) {
        query.groupIds = gids;
      }
    }

    const { rows: mongoUsers, total: totalCount } =
      await getUserRepository().listDirectory(orgId, query);

    const userIds = mongoUsers.map((u) => u._id);

    // Enrich with profile pictures, groups, and blocked status
    const [dpDocs, groupDocs, blockedIds] =
      userIds.length > 0
        ? await Promise.all([
            getPictureRepository().listSet(orgId, userIds),
            getGroupRepository().summariesForUsers(orgId, userIds),
            getCredentialRepository().blockedUserIds(orgId, userIds),
          ])
        : [[], [], []];

    const dpMap = new Map<string, string>();
    for (const dp of dpDocs) {
      if (dp.userId && dp.pic) {
        const mime = dp.mimeType || 'image/jpeg';
        dpMap.set(dp.userId, `data:${mime};base64,${dp.pic}`);
      }
    }

    const blockedUserIds = new Set<string | undefined>(blockedIds);

    // Build per-user group data
    const userGroupsMap = new Map<
      string,
      { _id: string; name: string; type: string }[]
    >();
    for (const g of groupDocs) {
      for (const uid of g.users) {
        const uidStr = uid;
        if (!userGroupsMap.has(uidStr)) userGroupsMap.set(uidStr, []);
        userGroupsMap
          .get(uidStr)!
          .push({ _id: g._id, name: g.name, type: g.type });
      }
    }

    const enrichedUsers = mongoUsers.map((u) => {
      const uid = u._id;
      const timestamps = u as typeof u & { createdAt?: Date; updatedAt?: Date };
      const groups = userGroupsMap.get(uid) ?? [];
      return {
        id: uid,
        userId: uid,
        orgId: u.orgId,
        name: u.fullName,
        email: u.email,
        // Carried through so a caller that asked for service accounts can tell
        // them apart. Without it the picker offers a machine identity with
        // nothing to mark it, which is worse than not offering it at all.
        kind: u.kind,
        isActive: !blockedUserIds.has(uid) && (u.hasLoggedIn ?? false),
        hasLoggedIn: u.hasLoggedIn ?? false,
        isBlocked: blockedUserIds.has(uid),
        createdAtTimestamp: timestamps.createdAt
          ? new Date(timestamps.createdAt).getTime()
          : undefined,
        updatedAtTimestamp: timestamps.updatedAt
          ? new Date(timestamps.updatedAt).getTime()
          : undefined,
        profilePicture: dpMap.get(uid),
        role: toDisplayUserRole(u.role),
        groupCount: groups.filter((g) => g.type !== 'everyone').length,
        userGroups: groups,
      };
    });

    res.status(200).json({
      users: enrichedUsers,
      pagination: buildPaginationMetadata(totalCount, page, limit),
    });
  }

  async getAllUsersWithGroups(
    req: AuthenticatedUserRequest,
    res: Response,
  ): Promise<void> {
    const { includeServiceAccounts } = req.query;
    const orgId = req.user?.orgId as string;

    // The same rule as getAllUsers above, for the same reason, and it matters
    // more here: this route returns each user's `userId` together with the
    // groups they belong to, and `userId` is exactly what an agent stores when
    // it is bound to a service account. Left open, any signed-in member could
    // read off the id of an account an administrator put in a sensitive group
    // and then bind an agent to it. Admin-only, and only when asked for, so no
    // existing caller changes behaviour — the people table does not ask.
    const wantsServiceAccounts = includeServiceAccounts === 'true';
    const maySeeServiceAccounts =
      wantsServiceAccounts &&
      req.user?.userId !== undefined &&
      (await isUserOrgAdmin(String(req.user.userId), String(orgId)));

    const users = await getUserRepository().listWithGroups(orgId, {
      includeServiceAccounts: maySeeServiceAccounts,
    });

    res.status(200).json(users);
  }

  async getUserById(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) {
    const userId = req.params.id;
    const orgId = req.user?.orgId;
    try {
      // Check if email should be included based on environment variable
      const hideEmail = process.env.HIDE_EMAIL === 'true';

      const user = await getUserRepository().getLive(
        orgId as string,
        userId as string,
      );

      if (!user) {
        throw new NotFoundError('User not found');
      }

      // A service account is a user record, so it would be returned here like
      // anyone else — including its `userId`, which is what binds an agent to
      // it. The screens that manage these accounts are admin-only and so is
      // the list of them, so this answers the way it would for an id that is
      // not there. Checked after the read rather than filtered into it, so the
      // ordinary case still costs one query.
      if (
        user.kind === 'service' &&
        !(
          req.user?.userId !== undefined &&
          (await isUserOrgAdmin(String(req.user.userId), String(orgId)))
        )
      ) {
        throw new NotFoundError('User not found');
      }

      if (hideEmail) {
        delete (user as any)?.email;
      }

      res.json(user);
    } catch (error) {
      next(error);
    }
  }

  async unblockUser(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const userId = req.params.id;
      const orgId = req.user?.orgId;

      if (!userId) {
        throw new BadRequestError('userId must be provided');
      }

      if (!orgId) {
        throw new BadRequestError('orgId must be provided');
      }

      const unblocked = await getCredentialRepository().unblock(
        userId,
        orgId as string,
      );

      if (!unblocked) {
        throw new BadRequestError('User not found or not blocked');
      }

      res.status(200).json({
        message: 'User unblocked successfully',
      });
    } catch (error) {
      next(error);
    }
  }

  async getUserEmailByUserId(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) {
    const userId = req.params.id;
    const orgId = req.user?.orgId;
    try {
      const user = await getUserRepository().getEmail(
        orgId as string,
        userId as string,
      );

      if (!user) {
        throw new NotFoundError('User not found');
      }

      res.json({ email: user.email });
    } catch (error) {
      next(error);
    }
  }

  async getUsersByIds(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { userIds }: { userIds: string[] } = req.body;

      // Validate if userIds is an array and not empty
      if (!userIds || !Array.isArray(userIds) || userIds.length === 0) {
        throw new BadRequestError(
          'userIds must be provided as a non-empty array',
        );
      }

      const invalidIds = userIds.filter((id) => !isValidId(id));
      if (invalidIds.length > 0) {
        throw new BadRequestError(
          'userIds must contain valid MongoDB ObjectIds',
        );
      }

      const orgId = req.user?.orgId;

      const found = await getUserRepository().listLiveByIds(
        orgId as string,
        userIds,
      );

      // Same rule as getUserById: a service account is a user record and would
      // come back here like anyone else, carrying the `userId` that binds an
      // agent to it. Callers ask this for the people behind agents, chats and
      // shares, so in practice nothing is dropped — and the admin lookup only
      // happens when a service account actually turns up.
      const hasServiceAccount = found.some((u) => u.kind === 'service');
      const maySeeServiceAccounts =
        hasServiceAccount &&
        req.user?.userId !== undefined &&
        (await isUserOrgAdmin(String(req.user.userId), String(orgId)));
      const users =
        hasServiceAccount && !maySeeServiceAccounts
          ? found.filter((u) => u.kind !== 'service')
          : found;

      const userIdStrs = users.map((u) => u._id);
      const dpMap = new Map<string, string>();
      if (orgId && userIdStrs.length > 0) {
        const dpDocs = await getPictureRepository().listSet(
          orgId as string,
          userIdStrs,
        );
        for (const dp of dpDocs) {
          if (dp.userId && dp.pic) {
            const mime = dp.mimeType || 'image/jpeg';
            dpMap.set(dp.userId, `data:${mime};base64,${dp.pic}`);
          }
        }
      }

      const enrichedUsers = users.map((u) => ({
        ...u,
        profilePicture: dpMap.get(u._id),
      }));

      res.status(200).json(enrichedUsers);
    } catch (error) {
      next(error);
    }
  }

  async checkUserExistsByEmail(
    req: AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      // The lookup is not org-scoped, so the email comes from the token the
      // auth service minted for this one sign-in, never from the request alone.
      // A USER_LOOKUP token minted for a userId carries no email and is refused.
      const tokenEmail = req.tokenPayload?.email;
      const { email } = req.body as { email: string };
      if (
        typeof tokenEmail !== 'string' ||
        tokenEmail.toLowerCase() !== email.toLowerCase()
      ) {
        throw new ForbiddenError(USER_LOOKUP_EMAIL_MISMATCH_MESSAGE);
      }

      const tokenOrgId = req.tokenPayload?.orgId;

      const lookup = (): ReturnType<
        ReturnType<typeof getUserRepository>['listLiveByEmail']
      > =>
        getUserRepository().listLiveByEmail(
          tokenEmail.toLowerCase(),
          USER_LOOKUP_FIELDS.split(' ') as Array<keyof UserDto>,
          tokenOrgId || undefined,
        );
      // a token that names no organisation looks the address up in the install's
      const users = tokenOrgId
        ? await lookup()
        : await inInstallOrg('address-lookup', lookup, []);

      res.json(users);
      return;
    } catch (error) {
      next(error);
    }
  }

  async createUser(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      // A starting password creates a sign-in-ready account without SMTP.
      // It is allowed only for the bundled demo personas: connector
      // permissions attach to an email address, so an admin who could set a
      // password for a real colleague's address would inherit everything
      // that colleague is allowed to see. The demo domain is IANA-reserved
      // and can never belong to a real person.
      const { password, ...userFields } = req.body as {
        password?: string;
        email?: string;
        [field: string]: unknown;
      };
      if (password !== undefined) {
        if (!isDemoAccountEmail(userFields.email)) {
          throw new BadRequestError(
            `A starting password can only be set for demo accounts (@${DEMO_ACCOUNT_DOMAIN}); invite real users so they choose their own`,
          );
        }
        if (!passwordValidator(password)) {
          throw new BadRequestError(
            'Password must be at least 8 characters with an uppercase letter, a lowercase letter, a number and a special character, and no longer than 72 bytes',
          );
        }
      }
      // Hashed before anything is written: a hash that fails here costs
      // nothing, whereas one that failed after the user was saved would leave
      // an account with no way to sign in and no way to retry creating it.
      const hashedPassword =
        password !== undefined
          ? await bcrypt.hash(password, SALT_ROUNDS)
          : undefined;
      const fields = {
        ...userFields,
        role: resolveOptionalUserRole(req.body.role),
      };
      const orgId = req.user?.orgId as string;
      const users = getUserRepository();

      // Refuse a duplicate here rather than letting the unique index throw
      // after side effects have happened. Addresses are stored lowercased.
      const email =
        typeof fields.email === 'string'
          ? fields.email.toLowerCase().trim()
          : '';
      if (email !== '') {
        if (await users.existsLiveEmail(email)) {
          // A Slack-only account at the address is upgraded, not refused.
          const existing = await users.getLiveByEmail(orgId, email);
          if (
            existing &&
            isSlackOnlyAccount(existing) &&
            password === undefined
          ) {
            res
              .status(201)
              .json(await this.addSlackOnlyAccountAsUser(existing, fields));
            return;
          }
          throw new BadRequestError('A user with this email already exists');
        }
        if (isDemoAccountEmail(email)) {
          await clearRemovedSampleAccount(email, orgId);
        }
      }

      // Persist the account and its credential before anything that is hard
      // to take back (the group membership, and the event the graph side
      // acts on). If a later write fails, undo what was saved so the address
      // is free to try again, and nothing has been published.
      // Whether the everyone-group write was reached. A write that threw may
      // still have applied, so the undo takes the membership back either way;
      // before it, there is nothing to take back.
      let groupWriteAttempted = false;
      let newUser!: UserDto;
      const undoSavedAccount = async (reason: string): Promise<void> => {
        // Membership goes first, so nothing is left pointing at a user that is
        // about to go, and in its own try: when the group write is what failed,
        // this is likely to fail too, and the account still has to go.
        try {
          if (groupWriteAttempted) {
            await getGroupRepository().removeFromEveryone(
              newUser.orgId,
              newUser._id,
            );
          }
        } catch (membershipError) {
          this.logger.warn(
            `Account was saved but ${reason}, and taking it out of the everyone group failed too`,
            {
              userId: newUser._id,
              error:
                membershipError instanceof Error
                  ? membershipError.message
                  : String(membershipError),
            },
          );
        }
        try {
          if (hashedPassword !== undefined) {
            await getCredentialRepository().discardForUser(
              newUser._id,
              newUser.email,
            );
          }
          await users.deleteById(newUser._id);
        } catch (cleanupError) {
          this.logger.error(
            `Account was saved but ${reason}, and removing it failed too`,
            {
              userId: newUser._id,
              error:
                cleanupError instanceof Error
                  ? cleanupError.message
                  : String(cleanupError),
            },
          );
        }
      };

      try {
        newUser = await users.create(orgId, fields);
      } catch (saveError) {
        // The check above only sees live accounts, but the unique index covers
        // every row, so the email can still collide here: two concurrent
        // creates, or an address held by a soft-deleted account. Either is a
        // refused duplicate, not a server error. Only the email key -- slug is
        // unique too, and a collision there is not a duplicate address.
        if (isDuplicateEmailKeyError(saveError)) {
          throw new BadRequestError('A user with this email already exists');
        }
        throw saveError;
      }
      if (hashedPassword !== undefined) {
        try {
          await getCredentialRepository().createWithPassword({
            userId: newUser._id,
            orgId: newUser.orgId,
            hashedPassword,
            ipAddress: req.ip,
          });
        } catch (credentialError) {
          await undoSavedAccount('its credential was not');
          throw credentialError;
        }
      }

      try {
        groupWriteAttempted = true;
        await getGroupRepository().addToEveryone(newUser.orgId, newUser._id);
      } catch (groupError) {
        await undoSavedAccount('the everyone-group membership was not');
        throw groupError;
      }

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.NewUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: newUser.orgId,
          userId: newUser._id,
          fullName: newUser.fullName,
          email: newUser.email,
          syncAction: SyncAction.Immediate,
        } as UserAddedEvent,
      };
      try {
        await this.eventService.publishEvent(event);
      } catch (publishError) {
        // The event is what the graph side acts on, and publishing writes an
        // outbox row that can fail by itself. Without this undo the address
        // stays taken by an account nothing downstream knows about, a demo
        // account with a password could already sign in, and a retry would hit
        // the unique email index.
        await undoSavedAccount('its creation event was not published');
        throw publishError;
      } finally {
        await this.eventService.stop();
      }
      if (hashedPassword !== undefined) {
        this.logger.info('Demo account created with a starting password', {
          orgId: newUser.orgId,
          createdBy: req.user?.userId,
          email: newUser.email,
        });
      }
      this.logger.debug('user created');
      res.status(201).json(newUser);
    } catch (error) {
      next(error);
    }
  }

  /**
   * "Add user" for an address that already has a Slack-only account: the same
   * record becomes an ordinary member (so its Slack threads stay its own),
   * takes the fields the admin entered, joins the everyone group and is
   * announced to the graph like any new user.
   */
  protected async addSlackOnlyAccountAsUser(
    existing: UserDto,
    entered: Record<string, unknown>,
  ): Promise<UserDto | null> {
    const role = entered.role === 'admin' ? 'admin' : 'member';
    if (role === 'admin') {
      await assertCanPromoteAdmin(existing.orgId, 1);
    }
    const profileKeys = [
      'fullName',
      'firstName',
      'lastName',
      'designation',
      'mobile',
    ] as const;
    const fields: Record<string, unknown> = { role };
    const previous: Record<string, unknown> = {
      role: existing.role ?? 'member',
    };
    for (const key of profileKeys) {
      if (entered[key] !== undefined) fields[key] = entered[key];
      previous[key] = existing[key];
    }
    if (!(await upgradeSlackOnlyAccount(existing._id, existing.orgId))) {
      throw new BadRequestError('A user with this email already exists');
    }
    let groupWriteAttempted = false;
    try {
      const upgraded = await getUserRepository().updateProfile(
        existing.orgId,
        existing._id,
        fields,
      );
      groupWriteAttempted = true;
      await getGroupRepository().addToEveryone(existing.orgId, existing._id);
      const payload: UserAddedEvent = {
        orgId: existing.orgId,
        userId: existing._id,
        fullName: upgraded?.fullName,
        email: existing.email,
        syncAction: SyncAction.Immediate,
      };
      await this.eventService.start();
      try {
        await this.eventService.publishEvent({
          eventType: EventType.NewUserEvent,
          timestamp: Date.now(),
          payload,
        });
      } finally {
        await this.eventService.stop();
      }
      return upgraded;
    } catch (error) {
      await this.undoSlackOnlyUpgrade(
        existing._id,
        existing.orgId,
        groupWriteAttempted,
        previous,
      );
      throw error;
    }
  }

  /**
   * Puts an account whose upgrade failed part way back to Slack-only, so a
   * retry finds it again instead of a half-made member. It held no group
   * before, so it leaves every group. Each step is best effort on its own:
   * a failed membership undo must not stop the record from being restored.
   */
  protected async undoSlackOnlyUpgrade(
    userId: string,
    orgId: string,
    groupWriteAttempted: boolean,
    previous: Record<string, unknown> = {},
  ): Promise<void> {
    const logFailure = (step: string, error: unknown): void => {
      this.logger.error(`Undoing a failed Slack-only upgrade: ${step} failed`, {
        userId,
        error: error instanceof Error ? error.message : String(error),
      });
    };
    if (groupWriteAttempted) {
      try {
        await getGroupRepository().removeFromAll(orgId, userId, {
          everyGroup: true,
        });
      } catch (membershipError) {
        logFailure('leaving the groups', membershipError);
      }
    }
    try {
      // A field present and undefined is cleared, as it was before.
      await getUserRepository().updateProfile(orgId, userId, {
        ...previous,
        kind: 'slack',
      });
    } catch (restoreError) {
      logFailure('restoring the record', restoreError);
    }
  }

  /**
   * Upgrades one invited Slack-only account and finishes the job (groups,
   * everyone, the new-user event) before anything else in the batch, undoing
   * the upgrade if a step fails. Returns whether it upgraded the account.
   */
  protected async completeSlackOnlyInvite(
    userId: string,
    email: string,
    orgId: string,
    groupIds: string[] | undefined,
  ): Promise<boolean> {
    if (!(await upgradeSlackOnlyAccount(userId, orgId))) return false;
    let groupWriteAttempted = false;
    try {
      groupWriteAttempted = true;
      if (groupIds && groupIds.length > 0) {
        await getGroupRepository().addMemberToGroups(orgId, groupIds, userId);
      }
      await getGroupRepository().addToEveryone(orgId, userId);
      const payload: UserAddedEvent = {
        orgId,
        userId,
        email,
        syncAction: SyncAction.Immediate,
      };
      await this.eventService.start();
      await this.eventService.publishEvent({
        eventType: EventType.NewUserEvent,
        timestamp: Date.now(),
        payload,
      });
      return true;
    } catch (error) {
      await this.undoSlackOnlyUpgrade(userId, orgId, groupWriteAttempted);
      throw error;
    }
  }

  /**
   * Generic Just-In-Time user provisioning for OAuth providers (Google, Microsoft, Azure AD, OAuth)
   * Creates user, adds to everyone group, and publishes creation event
   */
  async provisionJitUser(
    email: string,
    userDetails: { firstName?: string; lastName?: string; fullName: string },
    orgId: string,
    provider: 'google' | 'microsoft' | 'azureAd' | 'oauth',
    logger: Logger,
  ) {
    logger.info(`Auto-provisioning user from ${provider}`, { email, orgId });
    if (await getUserRepository().hasRemovedAccount(orgId, email)) {
      throw new BadRequestError(
        'User account deleted by admin. Please contact your admin to restore your account.',
      );
    }

    const newUser = await getUserRepository().create(orgId, {
      email,
      ...userDetails,
      hasLoggedIn: false,
      isDeleted: false,
      role: 'member',
    });

    // Add to everyone group
    await getGroupRepository().addToEveryone(orgId, newUser._id, {
      liveOnly: true,
    });

    // Publish user creation event
    try {
      await this.eventService.start();
      await this.eventService.publishEvent({
        eventType: EventType.NewUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: orgId,
          userId: newUser._id,
          fullName: newUser.fullName,
          email: newUser.email,
          syncAction: SyncAction.Immediate,
        } as UserAddedEvent,
      });
    } catch (eventError) {
      logger.error('Failed to publish user creation event', {
        error: eventError,
        userId: newUser._id,
      });
    } finally {
      await this.eventService.stop();
    }

    logger.info(`User auto-provisioned successfully via ${provider}`, {
      userId: newUser._id,
      email,
    });

    return newUser;
  }

  /**
   * Extract user details from Google ID token payload
   */
  extractGoogleUserDetails(payload: any, email: string) {
    const firstName = payload?.given_name;
    const lastName = payload?.family_name;
    const displayName = payload?.name;

    const fullName =
      displayName ||
      [firstName, lastName].filter(Boolean).join(' ') ||
      email.split('@')[0];

    return {
      firstName: firstName || undefined,
      lastName: lastName || undefined,
      fullName,
    };
  }

  /**
   * Extract user details from Microsoft/Azure AD decoded token
   */
  extractMicrosoftUserDetails(decodedToken: any, email: string) {
    const firstName = decodedToken?.given_name;
    const lastName = decodedToken?.family_name;
    const displayName = decodedToken?.name;

    const fullName =
      displayName ||
      [firstName, lastName].filter(Boolean).join(' ') ||
      email.split('@')[0];

    return {
      firstName: firstName || undefined,
      lastName: lastName || undefined,
      fullName,
    };
  }

  /**
   * Extract user details from OAuth userInfo response
   */
  extractOAuthUserDetails(userInfo: any, email: string) {
    // Common OAuth/OIDC claims
    const firstName =
      userInfo?.given_name || userInfo?.first_name || userInfo?.firstName;
    const lastName =
      userInfo?.family_name || userInfo?.last_name || userInfo?.lastName;
    const displayName =
      userInfo?.name || userInfo?.displayName || userInfo?.preferred_username;

    const fullName =
      displayName ||
      [firstName, lastName].filter(Boolean).join(' ') ||
      email.split('@')[0];

    return {
      firstName: firstName || undefined,
      lastName: lastName || undefined,
      fullName,
    };
  }

  async updateUser(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to update the user');
      }
      let emailChangeRequested = 'notNeeded';

      // Define whitelist of allowed fields that can be updated
      const ALLOWED_UPDATE_FIELDS = [
        'firstName',
        'lastName',
        'fullName',
        'middleName',
        'email',
        'designation',
        'mobile',
        'address',
        'dataCollectionConsent',
        'hasLoggedIn',
        'role',
      ] as const;
      // Fields that change account state rather than profile data; only an org
      // admin may set them, even on their own record.
      const ADMIN_ONLY_FIELDS = ['role', 'hasLoggedIn'] as const;

      // List of sensitive system fields that must never be updated via API
      const RESTRICTED_FIELDS = ['_id', 'orgId', 'slug', '__v'];

      // Check for restricted fields in request body
      const restrictedFieldsFound = RESTRICTED_FIELDS.filter(
        (field) => field in req.body,
      );
      if (restrictedFieldsFound.length > 0) {
        throw new BadRequestError(
          `Cannot update restricted fields: ${restrictedFieldsFound.join(', ')}`,
        );
      }

      // Role and login-state changes require org admin (userAdminOrSelfCheck
      // alone is not enough: it lets a member edit their own record).
      // After userExists, req.user is the target document but still carries JWT userId.
      const sentBody = req.body as Record<string, unknown>;
      // First sign-in marks the user's own record as logged in with the user's
      // own token (userAccount.controller); that one transition stays open, so
      // only an admin can reset someone to "Pending".
      const isSelfMarkedLoggedIn =
        req.params.id === String(req.user.userId) &&
        sentBody.hasLoggedIn === true;
      const adminOnlyFieldsSent = ADMIN_ONLY_FIELDS.filter(
        (field) =>
          field in sentBody &&
          sentBody[field] !== undefined &&
          !(field === 'hasLoggedIn' && isSelfMarkedLoggedIn),
      );
      if (adminOnlyFieldsSent.length > 0) {
        const actorUserId = req.user.userId;
        const requesterIsAdmin =
          !!actorUserId &&
          (await isUserOrgAdmin(
            actorUserId as string,
            req.user.orgId as string,
          ));
        if (!requesterIsAdmin) {
          throw new ForbiddenError(
            adminOnlyFieldsSent.includes('role')
              ? 'Only admins can change user roles'
              : 'Only admins can change whether a user has signed in',
          );
        }
      }
      if ('role' in req.body && req.body.role !== undefined) {
        const normalizedRole = normalizeUserRole(req.body.role);
        if (!normalizedRole) {
          throw new BadRequestError('Invalid role. Must be admin or member');
        }
        req.body.role = normalizedRole;
      }

      // Extract only allowed fields from request body
      const updateFields: Partial<
        Record<(typeof ALLOWED_UPDATE_FIELDS)[number], any>
      > = {};
      for (const field of ALLOWED_UPDATE_FIELDS) {
        if (field in req.body && req.body[field] !== undefined) {
          updateFields[field] = req.body[field];
        }
      }

      // If no valid fields to update, return error
      if (Object.keys(updateFields).length === 0) {
        throw new BadRequestError('No valid fields provided for update');
      }

      const { id } = req.params;
      const user = await getUserRepository().getForEdit(
        req.user.orgId as string,
        id as string,
      );

      if (!user) {
        throw new NotFoundError('User not found');
      }
      // Only the owner may change the address. Sending it unchanged, as a client
      // that writes back the whole record does, isn't a change. The route's
      // admin-or-self check has already refused anyone else before the lookup.
      if (
        updateFields.email !== undefined &&
        normalizedEmail(updateFields.email) !== normalizedEmail(user.email)
      ) {
        assertEmailChangeIsSelf(req.user.userId, id);
      }

      const orgId = req.user.orgId;
      // Unset/legacy role is treated as member so setting role=member is not a change.
      const previousRole = normalizeUserRole(user.role) ?? 'member';
      const roleChanging =
        updateFields.role !== undefined && updateFields.role !== previousRole;
      const demotingAdmin =
        updateFields.role === 'member' &&
        (user.role === 'admin' ||
          (user.role !== 'member' &&
            !!id &&
            !!orgId &&
            (await isUserOrgAdmin(id, orgId))));
      const promotingAdmin =
        updateFields.role === 'admin' && previousRole !== 'admin';

      if (demotingAdmin && (!id || !orgId)) {
        throw new BadRequestError('User or organization not found');
      }

      // Apply updates only for whitelisted fields
      // Separate email from other fields since it requires special handling (uniqueness check)
      const { email, ...otherUpdateFields } = updateFields;

      // What the account will look like once the other fields are written.
      const pendingUser = { ...user, ...otherUpdateFields };

      // Handle email update separately due to uniqueness check
      if (email !== undefined) {
        // Only update email if it's different from the current email
        const currentEmail = user.email?.toLowerCase().trim();
        const newEmail = email?.toLowerCase().trim();

        if (currentEmail !== newEmail) {
          // Stored addresses are lowercased and the unique index is
          // case-sensitive, so the raw request value can miss an existing
          // lowercase match and the change would only fail later on save.
          if (
            await getUserRepository().emailTakenByOther(
              req.user.orgId as string,
              newEmail as string,
              id as string,
            )
          ) {
            throw new BadRequestError('Email already exists for another user');
          }

          const emailSentResponse = await this.emailChange(
            email as string,
            newEmail as string,
            pendingUser,
          );

          if (emailSentResponse.statusCode !== 200) {
            emailChangeRequested = 'failed';
          } else {
            emailChangeRequested = 'sent';
          }
        }
      }

      // Demotion / promotion: RS = Org touch + check + save in one txn
      const rsAvailable = this.config.rsAvailable === 'true';
      let saved: UserDto | null;
      if (demotingAdmin && id && orgId) {
        saved = await saveUserEnsuringOrgRetainsAdmin(
          orgId as string,
          id,
          otherUpdateFields,
          rsAvailable,
        );
      } else if (promotingAdmin && id && orgId) {
        saved = await saveUserEnsuringAdminCap(
          orgId as string,
          id,
          otherUpdateFields,
          rsAvailable,
        );
      } else {
        saved = await getUserRepository().updateProfile(
          orgId as string,
          id as string,
          otherUpdateFields,
        );
      }
      if (!saved) {
        throw new NotFoundError('User not found');
      }
      const updatedUser = saved;

      // Role change: same as password change — invalidate every workspace
      // session for this email, then push force_logout to each connected tab.
      if (roleChanging && id && orgId) {
        try {
          const email = updatedUser.email;
          const allMemberships = email
            ? await getUserRepository().listLiveMembershipsByEmail(email)
            : [];
          const memberships =
            allMemberships.length > 0 ? allMemberships : [{ _id: id, orgId }];

          await getActivityRepository().recordMany(
            memberships.map((member) => ({
              orgId: String(member.orgId),
              userId: String(member._id),
              email,
              activityType: userActivitiesType.ROLE_CHANGED,
              ipAddress: req.ip || '',
            })),
          );

          const notificationService =
            NotificationContainer.getNotificationService();
          for (const member of memberships) {
            notificationService?.emitForceLogout(
              String(member._id),
              'role_changed',
            );
          }
        } catch (invalidateError) {
          this.logger.error('Failed to invalidate session after role change', {
            userId: id,
            orgId,
            error:
              invalidateError instanceof Error
                ? invalidateError.message
                : 'Unknown error',
          });
        }
      }

      await this.eventService.start();

      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: updatedUser.orgId,
          userId: updatedUser._id,
          fullName: updatedUser.fullName,
          ...(updatedUser.firstName && { firstName: updatedUser.firstName }),
          ...(updatedUser.lastName && { lastName: updatedUser.lastName }),
          ...(updatedUser.designation && {
            designation: updatedUser.designation,
          }),
          email: updatedUser.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      // Save the updated user
      res.json({
        ...updatedUser,
        meta: {
          emailChangeMailStatus: emailChangeRequested,
        },
      });
    } catch (error) {
      next(error);
    }
  }
  async updateFullName(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to update the user');
      }

      const { id } = req.params;
      const user = await getUserRepository().updateProfile(
        req.user.orgId as string,
        id as string,
        { fullName: req.body.fullName },
      );

      if (!user) {
        throw new NotFoundError('User not found');
      }

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: user.orgId,
          userId: user._id,
          fullName: user.fullName,
          ...(user.firstName && { firstName: user.firstName }),
          ...(user.lastName && { lastName: user.lastName }),
          ...(user.designation && { designation: user.designation }),
          email: user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      res.json(user);
    } catch (error) {
      next(error);
    }
  }

  async updateFirstName(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to update the user');
      }

      const { id } = req.params;
      const user = await getUserRepository().updateProfile(
        req.user.orgId as string,
        id as string,
        { firstName: req.body.firstName },
      );

      if (!user) {
        throw new NotFoundError('User not found');
      }

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: user.orgId,
          userId: user._id,
          fullName: user.fullName,
          ...(user.firstName && { firstName: user.firstName }),
          ...(user.lastName && { lastName: user.lastName }),
          ...(user.designation && { designation: user.designation }),
          email: user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      res.json(user);
    } catch (error) {
      next(error);
    }
  }

  async updateLastName(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to update the user');
      }

      const { id } = req.params;
      const user = await getUserRepository().updateProfile(
        req.user.orgId as string,
        id as string,
        { lastName: req.body.lastName },
      );

      if (!user) {
        throw new NotFoundError('User not found');
      }

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: user.orgId,
          userId: user._id,
          fullName: user.fullName,
          ...(user.firstName && { firstName: user.firstName }),
          ...(user.lastName && { lastName: user.lastName }),
          ...(user.designation && { designation: user.designation }),
          email: user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      res.json(user);
    } catch (error) {
      next(error);
    }
  }

  async updateDesignation(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to update the user');
      }

      const { id } = req.params;
      const user = await getUserRepository().updateProfile(
        req.user.orgId as string,
        id as string,
        { designation: req.body.designation },
      );

      if (!user) {
        throw new NotFoundError('User not found');
      }

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.UpdateUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: user.orgId,
          userId: user._id,
          fullName: user.fullName,
          ...(user.firstName && { firstName: user.firstName }),
          ...(user.lastName && { lastName: user.lastName }),
          ...(user.designation && { designation: user.designation }),
          email: user.email,
        } as UserUpdatedEvent,
      };

      await this.eventService.publishEvent(event);
      await this.eventService.stop();
      res.json(user);
    } catch (error) {
      next(error);
    }
  }

  async updateEmail(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to update the user');
      }

      const { id } = req.params;
      // Same rules as the email branch of updateUser: only the owner may change
      // the address. It is applied by /validateEmailChange once the link sent
      // to the new address is opened — never written here.
      const user = await getUserRepository().getForEdit(
        req.user.orgId as string,
        id as string,
      );

      if (!user) {
        throw new NotFoundError('User not found');
      }
      const body = req.body as { email?: unknown };
      const requested = typeof body.email === 'string' ? body.email : '';
      const newEmail = requested.toLowerCase().trim();
      if (newEmail === '') {
        throw new BadRequestError('email is required');
      }
      if (newEmail === normalizedEmail(user.email)) {
        res.json({ email: user.email, emailChangeMailStatus: 'notNeeded' });
        return;
      }
      assertEmailChangeIsSelf(req.user.userId, id);
      if (
        await getUserRepository().emailTakenByOther(
          req.user.orgId as string,
          newEmail,
          id as string,
        )
      ) {
        throw new BadRequestError('Email already exists for another user');
      }
      const emailSentResponse = await this.emailChange(
        requested,
        newEmail,
        user,
      );
      if (emailSentResponse.statusCode !== 200) {
        throw new InternalServerError(
          'Could not send the verification email to the new address',
        );
      }
      res.json({ email: user.email, emailChangeMailStatus: 'sent' });
    } catch (error) {
      next(error);
    }
  }

  /**
   * Soft-delete all OAuth apps owned by a user and revoke their tokens (when OAuth is initialized).
   */
  protected async softDeleteOAuthAppsForUser(
    orgId: unknown,
    createdByUserId: unknown,
    actorUser: Record<string, unknown>,
  ): Promise<void> {
    const orgOid = String(orgId);
    const creatorOid = String(createdByUserId);
    const apps = getOAuthAppRepository();

    const clientIds = await apps.listClientIdsCreatedBy(orgOid, creatorOid);

    const oauthTokenService = resolveOAuthTokenService();
    if (oauthTokenService && clientIds.length > 0) {
      for (const clientId of clientIds) {
        if (!clientId) {
          continue;
        }
        try {
          await oauthTokenService.revokeAllTokensForApp(clientId);
        } catch (err) {
          this.logger.error(
            'Failed to revoke OAuth tokens when deleting user',
            { clientId, err },
          );
        }
      }
    }

    const actorIdRaw = actorUser.userId ?? actorUser._id;
    const deletedBy = actorIdRaw != null ? String(actorIdRaw) : creatorOid;

    await apps.softDeleteCreatedBy(orgOid, creatorOid, deletedBy);
  }

  async deleteUser(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new UnauthorizedError('Unauthorized to delete the user');
      }
      const { id } = req.params;

      const user = await getUserRepository().getAny(
        req.user.orgId as string,
        id as string,
      );

      if (!user) {
        throw new NotFoundError('User not found');
      }

      const userId = user?._id;
      const orgId = user?.orgId;
      if (!userId || !orgId) {
        throw new NotFoundError('Account not found');
      }

      // Fail closed: only explicit members may be deleted (unset/legacy ≠ deletable).
      if (user.role !== 'member') {
        throw new BadRequestError(
          'User cannot be deleted. Please demote the user from admin first.',
        );
      }

      // Recorded first, before anything is changed: it ends the sessions and
      // refresh tokens issued before the deletion, and keeps them ended if the
      // account is later restored. A deletion without it would leave no
      // revocation marker, so if it cannot be written nothing is deleted.
      try {
        await getActivityRepository().record({
          orgId,
          userId,
          email: user.email,
          activityType: userActivitiesType.ACCOUNT_DELETED,
          ipAddress: req.ip ?? '',
        });
      } catch (activityError) {
        this.logger.error('Deletion not recorded; the user was not deleted', {
          userId: userId,
          error:
            activityError instanceof Error
              ? activityError.message
              : String(activityError),
        });
        throw markClientSafe(
          new ServiceUnavailableError(USER_DELETE_NOT_RECORDED),
        );
      }

      await getGroupRepository().removeFromAll(orgId, userId);

      await this.softDeleteOAuthAppsForUser(orgId, userId, req.user);

      // Revoke KB permissions BEFORE pulling memberships so a failed
      // revocation leaves the membership row intact — a retry of
      // deleteUser will re-find the same projects and reattempt.
      const projectsWithLinkedKb =
        await ProjectService.findProjectsWithLinkedKbForUser(orgId, userId);
      for (const project of projectsWithLinkedKb) {
        await ProjectKnowledgeBaseService.revokePrincipalPermission(
          this.config,
          req.headers as Record<string, string>,
          project,
          userId,
          'user',
        );
      }
      await ProjectService.removeUserFromAllProjects(orgId, userId);

      await getCredentialRepository().clearPassword(userId, user.email);
      await getUserRepository().markDeleted(orgId, userId, {
        signedOut: true,
        deletedBy: req.user?.userId ?? req.user?._id,
      });

      await this.eventService.start();
      const event: Event = {
        eventType: EventType.DeleteUserEvent,
        timestamp: Date.now(),
        payload: {
          orgId: user.orgId,
          userId: user._id,
          email: user.email,
        } as UserDeletedEvent,
      };
      await this.eventService.publishEvent(event);
      await this.eventService.stop();

      res.json({ message: 'User deleted successfully' });
    } catch (error) {
      next(error);
    }
  }

  async updateUserDisplayPicture(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const upload = readImageUpload(req.body as unknown);
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;

      if (upload === undefined) {
        throw new BadRequestError('DP File is required');
      }
      const dpBuffer = upload.buffer;
      assertRasterImage(
        dpBuffer,
        ['png', 'jpeg', 'webp', 'gif'],
        'Profile photo must be a PNG, JPEG, WebP or GIF image',
      );
      let quality = 100;
      let compressedImageBuffer = await sharp(dpBuffer)
        .jpeg({ quality })
        .toBuffer();
      while (compressedImageBuffer.length > 100 * 1024 && quality > 10) {
        quality -= 10;
        compressedImageBuffer = await sharp(dpBuffer)
          .jpeg({ quality })
          .toBuffer();
      }

      if (compressedImageBuffer.length > 100 * 1024) {
        throw new LargePayloadError('File too large , limit:1MB');
      }
      const compressedPic = compressedImageBuffer.toString('base64');
      const compressedPicMimeType = 'image/jpeg';

      await getPictureRepository().set(
        orgId as string,
        userId as string,
        compressedPic,
        compressedPicMimeType,
      );
      res.setHeader('Content-Type', compressedPicMimeType);
      res.status(201).send(compressedImageBuffer);
      return;
    } catch (error) {
      next(error);
    }
  }

  async getUserDisplayPicture(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;

      const userDp = await getPictureRepository().get(
        orgId as string,
        userId as string,
      );
      if (!userDp?.pic) {
        res.status(200).json({ errorMessage: 'User pic not found' });
        return;
      }

      const userDisplayBuffer = Buffer.from(userDp.pic, 'base64');
      if (userDp.mimeType) {
        res.setHeader('Content-Type', userDp.mimeType);
      }
      res.status(200).send(userDisplayBuffer);
      return;
    } catch (error) {
      next(error);
    }
  }

  async removeUserDisplayPicture(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;

      const userDp = await getPictureRepository().remove(
        orgId as string,
        userId as string,
      );

      if (!userDp) {
        res
          .status(200)
          .json({ errorMessage: 'User display picture not found' });
        return;
      }

      res.status(200).send(userDp);
    } catch (error) {
      next(error);
    }
  }

  async resendInvite(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { id } = req.params;
      if (!id) {
        throw new BadRequestError('Id is required');
      }

      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      const org = await getOrgRepository().getLive(req.user.orgId as string);
      const user = await getUserRepository().getForEdit(req.user.orgId, id);
      if (!user) {
        throw new UnauthorizedError('Error getting the user');
      }
      if (user?.hasLoggedIn) {
        throw new BadRequestError('User has already accepted the invite');
      }
      // Its invite would set a password on an account that cannot sign in.
      // Inviting the address upgrades it instead.
      if (isSlackOnlyAccount(user)) {
        throw new BadRequestError(
          'This account was created from Slack. Invite the address to make it a member.',
        );
      }

      const email = user?.email;
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const authToken = fetchConfigJwtGenerator(
        userId as string,
        orgId as string,
        this.config.scopedJwtSecret,
      );
      let result = await this.authService.passwordMethodEnabled(authToken);

      if (result.statusCode !== 200) {
        throw new InternalServerError('Error fetching auth methods');
      }
      if (result.data?.isPasswordAuthEnabled) {
        const { passwordResetToken, mailAuthToken } =
          jwtGeneratorForNewAccountPassword(
            email,
            id,
            orgId as string,
            this.config.scopedJwtSecret,
          );

        result = await this.mailService.sendMail({
          emailTemplateType: 'appuserInvite',
          initiator: {
            jwtAuthToken: mailAuthToken,
            orgId: orgId?.toString(),
          },
          usersMails: [email],
          subject: `You are invited to join ${org?.registeredName} `,
          templateData: {
            invitee: user?.fullName,
            orgName: org?.shortName || org?.registeredName,
            link: `${this.config.frontendUrl}/reset-password#token=${passwordResetToken}`,
          },
        });
        if (result.statusCode !== 200) {
          this.logger.error('Sending the invitation failed', {
            statusCode: result.statusCode,
            reason: result.data,
          });
          throw mailFailureError(result, 'send the invitation');
        }
      } else {
        result = await this.mailService.sendMail({
          emailTemplateType: 'appuserInvite',
          initiator: {
            jwtAuthToken: mailJwtGenerator(email, this.config.scopedJwtSecret),
            orgId: orgId?.toString(),
          },
          usersMails: [email],
          subject: `You are invited to join ${org?.registeredName} `,
          templateData: {
            invitee: user?.fullName,
            orgName: org?.shortName || org?.registeredName,
            link: `${this.config.frontendUrl}/sign-in`,
          },
        });
        if (result.statusCode !== 200) {
          this.logger.error('Sending the invitation failed', {
            statusCode: result.statusCode,
            reason: result.data,
          });
          throw mailFailureError(result, 'send the invitation');
        }
      }

      res.status(200).json({ message: 'Invite sent successfully' });
      return;
    } catch (error) {
      next(error);
    }
  }

  /**
   * Members may invite only as member and may not attach groupIds.
   * Admin role / group assignment stay org-admin only.
   */
  protected async assertMemberInviteConstraints(
    actorUserId: string | undefined,
    orgId: string | undefined,
    inviteRole: 'admin' | 'member',
    groupIds?: unknown,
  ): Promise<void> {
    const wantsAdminRole = inviteRole === 'admin';
    const wantsGroups = Array.isArray(groupIds) && groupIds.length > 0;
    if (!wantsAdminRole && !wantsGroups) {
      return;
    }

    const actorIsAdmin =
      !!actorUserId && !!orgId && (await isUserOrgAdmin(actorUserId, orgId));
    if (actorIsAdmin) {
      return;
    }
    if (wantsAdminRole) {
      throw new ForbiddenError('Members can only invite users as member');
    }
    throw new ForbiddenError('Members cannot assign groups when inviting');
  }

  async addManyUsers(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      const { emails, groupIds, role } = req.body;

      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      if (!emails) {
        throw new BadRequestError('emails are required');
      }
      if (!Array.isArray(emails)) {
        throw new BadRequestError('Please provide an array of email addresses');
      }

      const invalidEmails = emails.filter((email) => !isValidEmail(email));
      if (invalidEmails.length > 0) {
        throw new BadRequestError('Invalid emails are found');
      }

      // role is validated by bulkInviteValidationSchema when present
      const inviteRole = role === 'admin' ? 'admin' : 'member';
      await this.assertMemberInviteConstraints(
        req.user.userId as string,
        req.user.orgId as string,
        inviteRole,
        groupIds,
      );

      const orgId = req.user.orgId;
      const org = await getOrgRepository().getLive(orgId as string);
      const normalizedEmails = this.normalizeEmails(emails);

      await this.eventService.start();
      // Deliberately not calling eventService.stop(): the MessageProducer is a
      // shared singleton (connected at startup, disconnected in the container's
      // dispose()). Stopping it here would drop the connection for concurrent
      // requests and for the background task in addManyUsersFromFile.
      const result = await this.processInvites(
        normalizedEmails,
        groupIds,
        orgId as string,
        req.user?.fullName,
        org,
        inviteRole,
      );

      if (
        result.invited === 0 &&
        result.restored === 0 &&
        result.reinvited === 0
      ) {
        throw new ConflictError(
          'All provided emails already have active accounts',
        );
      }

      if (result.mailFailed.length > 0) {
        res.status(result.mailErrorCode ?? 500).json({
          message: 'Error sending mail invite. Please try again.',
        });
        return;
      }

      res.status(200).json({
        message:
          'Invites queued. Emails are being sent in the background and may take a few minutes.',
        queued: true,
        invited: result.invited,
        reinvited: result.reinvited,
        restored: result.restored,
        alreadyActive: result.alreadyActive,
      });
    } catch (error) {
      next(error);
    }
  }

  async addManyUsersFromFile(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    try {
      if (!req.user) {
        throw new NotFoundError('User not found');
      }

      const file = req.body.fileBuffer as FileBufferInfo | undefined;
      if (!file) {
        throw new BadRequestError('No file uploaded');
      }

      let parsedEmails: string[];
      try {
        parsedEmails = this.parseEmailsFromFile(file.buffer);
      } catch {
        throw new BadRequestError(
          'Could not read the file. Upload a valid CSV or Excel file.',
        );
      }

      const normalizedEmails = this.normalizeEmails(parsedEmails);
      if (normalizedEmails.length === 0) {
        throw new BadRequestError('No email addresses found in the file');
      }
      if (normalizedEmails.length > MAX_BULK_INVITE) {
        throw new BadRequestError(
          `File exceeds the ${MAX_BULK_INVITE}-user limit`,
        );
      }

      let groupIds: string[] | undefined;
      if (typeof req.body.groupIds === 'string' && req.body.groupIds) {
        let parsed: unknown;
        try {
          parsed = JSON.parse(req.body.groupIds);
        } catch {
          parsed = undefined;
        }
        if (!Array.isArray(parsed)) {
          throw new BadRequestError(
            'groupIds must be a JSON array of group ids, such as ["<id>"]',
          );
        }
        groupIds = parsed;
      } else if (Array.isArray(req.body.groupIds)) {
        groupIds = req.body.groupIds;
      }
      // Reject invalid ids on the request thread — otherwise Mongo throws a
      // CastError deep in the background task, surfacing only as a generic
      // failure notification.
      if (groupIds?.some((id) => !isValidId(id))) {
        throw new BadRequestError(
          'groupIds must contain valid MongoDB ObjectIds',
        );
      }

      await this.assertMemberInviteConstraints(
        req.user.userId as string,
        req.user.orgId as string,
        'member',
        groupIds,
      );

      const orgId = req.user.orgId;
      const inviterUserId = req.user?.userId;
      const inviterName = req.user?.fullName;
      const org = await getOrgRepository().getLive(orgId as string);

      const validEmails = normalizedEmails.filter((email) =>
        isValidEmail(email),
      );
      const invalidEmails = normalizedEmails.filter(
        (email) => !isValidEmail(email),
      );

      res.status(202).json({
        message: 'Import started. You will be notified when it finishes.',
      });

      setImmediate(async () => {
        // ponytail: in-process fire-and-forget — a crash mid-batch loses the
        // remaining invites. Move behind a broker consumer if durability matters.
        try {
          await this.eventService.start();
          const result = await this.processInvites(
            validEmails,
            groupIds,
            orgId as string,
            inviterName,
            org,
          );
          await this.notifyInviteResult(inviterUserId, orgId, {
            ...result,
            invalid: invalidEmails,
          });
          // Deliberately not calling eventService.stop(): the MessageProducer is
          // a shared singleton (connected at startup, disconnected in the
          // container's dispose()). Stopping it here would drop the connection
          // for concurrent requests and the notification producer.
        } catch (error) {
          this.logger.error('Bulk invite from file failed', error);
          await this.notifyInviteFailure(inviterUserId, orgId);
        }
      });
    } catch (error) {
      next(error);
    }
  }

  protected normalizeEmails(emails: string[]): string[] {
    const seen = new Set<string>();
    const result: string[] = [];
    for (const raw of emails) {
      if (raw == null) continue;
      const email = String(raw)
        .trim()
        .replace(/^\uFEFF/, '')
        .toLowerCase();
      if (email && !seen.has(email)) {
        seen.add(email);
        result.push(email);
      }
    }
    return result;
  }

  protected parseEmailsFromFile(buffer: Buffer): string[] {
    // Excel exports UTF-8 CSVs with a leading BOM; strip it so it never leaks
    // into the first cell, and force UTF-8 so non-ASCII text isn't mis-decoded.
    let input = buffer;
    if (
      input.length >= 3 &&
      input[0] === 0xef &&
      input[1] === 0xbb &&
      input[2] === 0xbf
    ) {
      input = input.subarray(3);
    }
    // Cap rows read so a small but densely-packed upload can't inflate into a
    // huge sheet and block the event loop; the MAX_BULK_INVITE check runs later
    // on the extracted emails, so a header row + the limit is enough headroom.
    const workbook = XLSX.read(input, {
      type: 'buffer',
      codepage: 65001,
      sheetRows: MAX_BULK_INVITE + 5,
    });
    const emails: string[] = [];
    for (const sheetName of workbook.SheetNames) {
      const sheet = workbook.Sheets[sheetName];
      if (!sheet) continue;
      const rows = XLSX.utils.sheet_to_json<unknown[]>(sheet, {
        header: 1,
        blankrows: false,
      });
      for (const row of rows) {
        for (const cell of row) {
          if (cell == null) continue;
          const value = String(cell).trim();
          // 254 = max email length per RFC 5321; skip longer cells so a huge
          // malformed value never bloats the parsed/invalid list.
          if (value.length <= 254 && value.includes('@')) {
            emails.push(value);
          }
        }
      }
    }
    return emails;
  }

  protected async processInvites(
    emails: string[],
    groupIds: string[] | undefined,
    orgId: string,
    inviterName: string | undefined,
    org: { registeredName?: string; shortName?: string } | null,
    inviteRole: 'admin' | 'member' = 'member',
  ): Promise<InviteResult> {
    // Service-account addresses are not invitable, and are dropped before
    // anything is derived from the list. They belong to machine identities
    // managed on their own screen: inviting one would try to send mail to a
    // domain that does not resolve, and a deleted one would be restored as a
    // person — which would also let an invite occupy a name the service
    // account screen then could not use. They must not count toward the
    // administrator limit either.
    emails = emails.filter((email) => !isServiceAccountEmail(email));

    const users = getUserRepository();
    const groups = getGroupRepository();
    const existingUsers = (await users.listByEmails(orgId, emails)).filter(
      (user) => user.kind !== 'service',
    );
    const activeUsers = existingUsers.filter((user) => !user.isDeleted);
    const deletedUsers = existingUsers.filter((user) => user.isDeleted);

    const activeEmails = activeUsers.map((user) => user.email);
    const deletedEmails = deletedUsers.map((user) => user.email);
    const pendingUsers = activeUsers.filter((user) => !user.hasLoggedIn);
    const pendingUserIds = pendingUsers.map((user) => user._id).filter(Boolean);

    const blockedPendingUserIds = new Set(
      pendingUserIds.length > 0
        ? await getCredentialRepository().blockedUserIds(orgId, pendingUserIds)
        : [],
    );
    const pendingUsersToReinvite = pendingUsers.filter(
      (user) => user._id && !blockedPendingUserIds.has(user._id),
    );

    const emailsForNewAccounts = emails.filter(
      (email) =>
        !activeEmails.includes(email) && !deletedEmails.includes(email),
    );

    if (inviteRole === 'admin') {
      const additionalAdmins =
        emailsForNewAccounts.length +
        deletedUsers.length +
        pendingUsersToReinvite.filter(
          (user) => normalizeUserRole(user.role) !== 'admin',
        ).length;
      await assertCanPromoteAdmin(orgId, additionalAdmins);
    }

    let restoredUsers: UserDto[] = [];
    if (deletedUsers.length > 0) {
      // Recorded before the accounts come back, and nothing is restored without it:
      // it ends every token issued before the restore, including any minted while
      // the deletion was still running.
      await getActivityRepository().recordMany(
        deletedUsers.map((user) => ({
          orgId,
          userId: user._id,
          email: user.email,
          activityType: userActivitiesType.ACCOUNT_RESTORED,
          ipAddress: 'system',
        })),
      );
      restoredUsers = await users.restoreByEmails(orgId, deletedEmails);
    }

    // Inviting a Slack-only account upgrades it in place, so the Slack threads
    // it already has stay its own. Each upgrade is finished (groups, event)
    // or undone before the batch goes on. A blocked one is left as it is.
    const upgradedIds = new Set<string>();
    for (const user of [...pendingUsersToReinvite, ...restoredUsers]) {
      if (
        isSlackOnlyAccount(user) &&
        (await this.completeSlackOnlyInvite(
          user._id,
          user.email,
          orgId,
          groupIds,
        ))
      ) {
        upgradedIds.add(user._id);
      }
    }

    for (let i = 0; i < existingUsers.length; ++i) {
      const userId = existingUsers[i]?._id;
      // Slack-only accounts were either finished above or left alone.
      if (isSlackOnlyAccount(existingUsers[i])) {
        continue;
      }
      if (groupIds && groupIds.length > 0) {
        await groups.addMemberToGroups(orgId, groupIds, userId as string);
      }
      await groups.addToEveryone(orgId, userId as string);
    }

    let newUsers: UserDto[] = [];
    if (emailsForNewAccounts.length > 0) {
      newUsers = await users.createMany(
        orgId,
        emailsForNewAccounts.map((email) => ({
          email,
          isDeleted: false,
          hasLoggedIn: false,
          role: inviteRole,
        })),
      );
    }

    // Apply invite role to restored / re-invited pending users when promoting to admin
    if (inviteRole === 'admin') {
      const promoteIds = [
        ...restoredUsers.map((u) => u._id),
        ...pendingUsersToReinvite.map((u) => u._id),
      ].filter(Boolean);
      if (promoteIds.length > 0) {
        await users.promoteToAdmin(orgId, promoteIds);
      }
    }

    // Password-auth is an org-wide setting, so resolve it once for the whole
    // batch instead of once per recipient (previously N internal HTTP calls).
    // Any user in the org yields the same answer; probe with the first we have.
    const probeUserId =
      newUsers[0]?._id?.toString() ??
      pendingUsersToReinvite[0]?._id?.toString() ??
      restoredUsers[0]?._id?.toString();
    let isPasswordAuthEnabled = false;
    if (probeUserId !== undefined && probeUserId !== '') {
      const authToken = fetchConfigJwtGenerator(
        probeUserId,
        orgId,
        this.config.scopedJwtSecret,
      );
      const authResult =
        await this.authService.passwordMethodEnabled(authToken);
      if (authResult.statusCode !== 200) {
        throw new InternalServerError('Error fetching auth methods');
      }
      isPasswordAuthEnabled = Boolean(authResult.data?.isPasswordAuthEnabled);
    }

    const mailFailed: string[] = [];
    let mailErrorCode: number | undefined;
    const newUserByEmail = new Map(newUsers.map((user) => [user.email, user]));
    const pendingUserByEmail = new Map(
      pendingUsersToReinvite.map((user) => [user.email, user]),
    );
    const emailsForPendingAccounts = pendingUsersToReinvite
      .map((user) => user.email)
      .filter((email): email is string => Boolean(email));
    const emailsForInvites = [
      ...emailsForNewAccounts,
      ...emailsForPendingAccounts,
    ];

    for (let i = 0; i < emailsForInvites.length; ++i) {
      const email = emailsForInvites[i];
      if (email === undefined || email === '') continue;
      const pendingUser = pendingUserByEmail.get(email);
      const userId = pendingUser?._id ?? newUserByEmail.get(email)?._id;
      if (userId === undefined || userId === '') {
        throw new InternalServerError(
          'User ID missing while inviting user. Please ensure user creation was successful.',
        );
      }
      if (!pendingUser) {
        if (groupIds && groupIds.length > 0) {
          await groups.addMemberToGroups(orgId, groupIds, userId);
        }
        await groups.addToEveryone(orgId, userId);
        const event: Event = {
          eventType: EventType.NewUserEvent,
          timestamp: Date.now(),
          payload: {
            orgId: orgId,
            userId: userId,
            email,
            syncAction: SyncAction.Immediate,
          } as UserAddedEvent,
        };
        // A concurrent request may have disconnected the shared producer;
        // start() is idempotent and reconnects so the event isn't dropped.
        await this.eventService.start();
        await this.eventService.publishEvent(event);
      }

      const statusCode = await this.sendInviteMail(
        email,
        userId,
        orgId,
        inviterName,
        org,
        false,
        isPasswordAuthEnabled,
      );
      if (statusCode !== 200) {
        mailFailed.push(email);
        mailErrorCode = statusCode;
      }
    }

    for (let i = 0; i < restoredUsers.length; ++i) {
      const email = restoredUsers[i]?.email;
      const userId = restoredUsers[i]?._id;
      if (email === undefined || email === '') {
        continue;
      }
      if (!userId) {
        throw new InternalServerError(
          'User ID missing while inviting restored user. Please ensure user restoration was successful.',
        );
      }
      // An upgraded Slack-only account was announced when it was upgraded.
      if (!upgradedIds.has(userId)) {
        const event: Event = {
          eventType: EventType.NewUserEvent,
          timestamp: Date.now(),
          payload: {
            orgId: orgId,
            userId: userId,
            email,
            syncAction: SyncAction.Immediate,
          } as UserAddedEvent,
        };
        await this.eventService.start();
        await this.eventService.publishEvent(event);
      }

      const statusCode = await this.sendInviteMail(
        email,
        userId,
        orgId,
        inviterName,
        org,
        true,
        isPasswordAuthEnabled,
      );
      if (statusCode !== 200) {
        mailFailed.push(email);
        mailErrorCode = statusCode;
      }
    }

    return {
      invited: newUsers.length,
      restored: restoredUsers.length,
      reinvited: pendingUsersToReinvite.length,
      alreadyActive: activeUsers.length - pendingUsersToReinvite.length,
      mailFailed,
      mailErrorCode,
    };
  }

  private async sendInviteMail(
    email: string,
    userId: string,
    orgId: string,
    inviterName: string | undefined,
    org: { registeredName?: string; shortName?: string } | null,
    rejoin: boolean,
    isPasswordAuthEnabled: boolean,
  ): Promise<number> {
    const subject = `You are invited to ${rejoin ? 're-join' : 'join'} ${org?.registeredName} `;
    const invitee = inviterName;
    const orgName = org?.shortName || org?.registeredName;

    // A transport failure for one recipient must not abort the rest of the
    // batch: return a non-200 so the caller records it in mailFailed instead.
    try {
      // Always queued: even a handful of sequential inline sends can hold the
      // request for minutes when SMTP is slow.
      const result = await this.mailService.sendMail({
        emailTemplateType: 'appuserInvite',
        initiator: {
          jwtAuthToken: mailJwtGenerator(email, this.config.scopedJwtSecret),
          orgId: orgId,
        },
        usersMails: [email],
        subject,
        templateData: isPasswordAuthEnabled
          ? { invitee, orgName }
          : { invitee, orgName, link: `${this.config.frontendUrl}/sign-in` },
        ...(isPasswordAuthEnabled && {
          passwordResetLinkFor: { userId, orgId: orgId, email },
        }),
        deliverAsync: true,
      });
      return result.statusCode;
    } catch (error) {
      this.logger.error(`Failed to send invite mail to ${email}`, error);
      return 500;
    }
  }

  protected async notifyInviteResult(
    userId: string | undefined,
    orgId: string,
    summary: InviteResult & { invalid: string[] },
  ): Promise<void> {
    if (userId === undefined || userId === '') return;
    const parts = [
      `${summary.invited} invited`,
      `${summary.reinvited} re-invited`,
      `${summary.restored} restored`,
      `${summary.alreadyActive} already members`,
      `${summary.invalid.length} invalid`,
    ];
    if (summary.mailFailed.length > 0) {
      parts.push(`${summary.mailFailed.length} failed to email`);
    }
    const severity =
      summary.mailFailed.length > 0 || summary.invalid.length > 0
        ? 'warning'
        : 'success';
    await this.publishInviteNotification(
      userId,
      orgId,
      'Bulk invite finished',
      parts.join(', '),
      severity,
      { invalid: summary.invalid, mailFailed: summary.mailFailed },
    );
  }

  protected async notifyInviteFailure(
    userId: string | undefined,
    orgId: string,
  ): Promise<void> {
    if (userId === undefined || userId === '') return;
    await this.publishInviteNotification(
      userId,
      orgId,
      'Bulk invite failed',
      'The uploaded file could not be processed. Please try again.',
      'error',
      {},
    );
  }

  protected async publishInviteNotification(
    userId: string,
    orgId: string,
    title: string,
    message: string,
    severity: string,
    payload: Record<string, unknown>,
  ): Promise<void> {
    try {
      await this.notificationProducer.start();
      await this.notificationProducer.publishEvent({
        eventType: NotificationEventType.NewNotificationEvent,
        timestamp: Date.now(),
        payload: {
          orgId: orgId,
          type: 'user.bulkInvite',
          recipientUserIds: [userId],
          title,
          message,
          severity,
          status: 'unread',
          payload,
        } as unknown as NotificationEventPayload,
      });
    } catch (error) {
      this.logger.error('Failed to publish bulk invite notification', error);
    }
  }

  async listUsers(
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> {
    const requestId = req.context?.requestId;
    try {
      const orgId = req.user?.orgId;
      const userId = req.user?.userId;
      if (!orgId) {
        throw new BadRequestError('Organization ID is required');
      }
      if (!userId) {
        throw new BadRequestError('User ID is required');
      }

      const { page, limit, search, includeServiceAccounts } = req.query;

      // Validate search parameter for XSS and format specifiers
      if (search) {
        try {
          validateNoXSS(String(search), 'search parameter');
          validateNoFormatSpecifiers(String(search), 'search parameter');

          if (String(search).length > 1000) {
            throw new BadRequestError(
              'Search parameter too long (max 1000 characters)',
            );
          }
        } catch (error: any) {
          throw new BadRequestError(
            error.message ||
              'Search parameter contains potentially dangerous content',
          );
        }
      }

      const queryParams = new URLSearchParams();
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (search) queryParams.append('search', String(search));
      // Forwarded, not decided here: the connector service holds the query that
      // returns these rows, and it checks the caller's role before honouring it.
      if (includeServiceAccounts === 'true') {
        queryParams.append('includeServiceAccounts', 'true');
      }
      const queryString = queryParams.toString();

      const aiCommandOptions: AICommandOptions = {
        uri: `${this.config.connectorBackend}/api/v1/entity/user/list?${queryString}`,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        method: HttpMethod.GET,
      };
      const aiCommand = new AIServiceCommand<GraphUserListResponse>(
        aiCommandOptions,
      );
      const aiResponse = await aiCommand.execute();
      if (aiResponse && aiResponse.statusCode !== 200) {
        throw new BadRequestError('Failed to get users');
      }
      const data = aiResponse.data;
      const usersArray = data?.users ?? [];

      const userMongoIds = usersArray.map((u) => u.userId).filter(Boolean);

      if (userMongoIds.length > 0) {
        // Parallel: fetch DPs, mongo user docs (hasLoggedIn), and group memberships
        const [dpDocs, mongoUsers, groupDocs] = await Promise.all([
          getPictureRepository().listSet(orgId as string, userMongoIds),
          getUserRepository().listSummariesByIds(orgId as string, userMongoIds),
          getGroupRepository().summariesForUsers(orgId as string, userMongoIds),
        ]);

        // Build lookup maps
        const dpMap = new Map<string, string>();
        for (const dp of dpDocs) {
          if (dp.userId && dp.pic) {
            const mime = dp.mimeType || 'image/jpeg';
            dpMap.set(dp.userId, `data:${mime};base64,${dp.pic}`);
          }
        }

        const mongoUserMap = new Map<
          string,
          { hasLoggedIn?: boolean; fullName?: string; role?: string }
        >();
        for (const mu of mongoUsers) {
          mongoUserMap.set(mu._id, {
            hasLoggedIn: mu.hasLoggedIn,
            fullName: mu.fullName,
            role: mu.role,
          });
        }

        // Build per-user groups
        const userGroupsMap = new Map<string, UserGroupSummary[]>();
        for (const g of groupDocs) {
          for (const uid of g.users) {
            const uidStr = uid;
            if (!userGroupsMap.has(uidStr)) {
              userGroupsMap.set(uidStr, []);
            }
            userGroupsMap.get(uidStr)!.push({
              _id: g._id,
              name: g.name,
              type: g.type,
            });
          }
        }

        // Enrich each user
        for (const user of usersArray) {
          const uid = user.userId;
          if (!uid) continue;
          if (dpMap.has(uid)) user.profilePicture = dpMap.get(uid);
          const mu = mongoUserMap.get(uid);
          if (mu) {
            user.hasLoggedIn = mu.hasLoggedIn ?? true;
            if (!user.name && mu.fullName) user.name = mu.fullName;
          }
          const groups = userGroupsMap.get(uid) ?? [];
          user.userGroups = groups;
          user.groupCount = groups.filter((g) => g.type !== 'everyone').length;
          const mongoRole = mongoUserMap.get(uid)?.role;
          user.role = toDisplayUserRole(mongoRole);
        }
      }

      res.status(HTTP_STATUS.OK).json(data);
    } catch (error: any) {
      this.logger.error('Error getting users', {
        requestId,
        message: 'Error getting users',
        error: error.message,
      });
      next(error);
    }
  }

  async emailChange(
    email: string,
    newEmail: string,
    user: Record<string, any>,
    // authToken: string,
  ) {
    try {
      if (!email) {
        throw new BadRequestError('Email is required');
      }
      const result = await this.sendValidateEmailIdEmail(user, newEmail);

      if (result.statusCode !== 200) {
        return {
          statusCode: 400,
          data: 'Failed to send email',
        };
      }
      return {
        statusCode: 200,
        data: 'Email change verification mail sent',
      };
      // res.status(200).send({ data: 'email change verification mail sent' });
      // return;
    } catch (error) {
      return {
        statusCode: 400,
        data: 'Failed to send email',
      };
    }
  }

  async sendValidateEmailIdEmail(user: Record<string, any>, newEmail: string) {
    try {
      const { validateEmailToken, mailAuthToken } =
        jwtGeneratorForValidateEmailLink(
          user.email,
          newEmail,
          user._id,
          user.orgId,
          this.config.scopedJwtSecret,
        );

      const validateEmailLink = `${this.config.frontendUrl}/reset-email#token=${validateEmailToken}`;
      const org = await getOrgRepository().getLive(String(user.orgId));
      const emailSentResponse = await this.mailService.sendMail({
        emailTemplateType: 'resetEmail',
        initiator: {
          jwtAuthToken: mailAuthToken,
          orgId: user.orgId?.toString(),
        },
        usersMails: [newEmail],
        subject: `${getBrandConfig().productName} | Verify your email !`,
        templateData: {
          orgName: org?.shortName || org?.registeredName,
          name: user.fullName,
          link: validateEmailLink,
        },
      });

      if (emailSentResponse.statusCode !== 200) {
        return {
          statusCode: 400,
          data: 'Failed to send email',
        };
      }

      // Tell the current address too, so an account moved by someone who
      // has the user's session is not moved silently. Best effort: the
      // verification mail is what matters, and the change still needs the
      // link at the new address to be opened.
      const currentEmail = typeof user.email === 'string' ? user.email : '';
      const userIdForLog = String(user._id ?? '');
      try {
        const notice = await this.mailService.sendMail({
          emailTemplateType: 'emailChangeNotice',
          initiator: {
            jwtAuthToken: mailAuthToken,
            orgId: String(user.orgId ?? ''),
          },
          usersMails: [currentEmail],
          subject: `${getBrandConfig().productName} | Your email address is being changed`,
          templateData: {
            orgName: org?.shortName ?? org?.registeredName,
            name: user.fullName,
            newEmail,
          },
        });
        if (notice.statusCode !== 200) {
          this.logger.warn(
            'Email-change notice to the current address was not sent',
            { userId: userIdForLog, statusCode: notice.statusCode },
          );
        }
      } catch (noticeError) {
        this.logger.warn('Email-change notice to the current address failed', {
          userId: userIdForLog,
          error:
            noticeError instanceof Error
              ? noticeError.message
              : String(noticeError),
        });
      }

      return {
        statusCode: 200,
        data: 'mail sent',
      };
    } catch (error) {
      return {
        statusCode: 400,
        data: 'Failed to send email',
      };
    }
  }
}
