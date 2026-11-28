// Copyright (c) 2026 PipesHub Inc.
// Licensed under the PipesHub Enterprise License. See the LICENSE file in this
// ee folder. Free for development, testing and evaluation. Production use needs
// a PipesHub licence key.

import {
  Router,
  type NextFunction,
  type Request,
  type RequestHandler,
  type Response,
} from 'express';
import type { Container } from 'inversify';
import { spawnDetached } from '../../../libs/context/orgContext';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { NotFoundError } from '../../../libs/errors/http.errors';
import {
  noopEditionBoot,
  type EditionBoot,
} from '../../../libs/edition/edition-boot';
import type { SeatGuard } from '../../../libs/edition/seat-guard';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import type { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { getIdentityPort } from '../../../libs/repositories/identity';
import { inInstallOrg } from '../../../libs/repositories/identity/install-org';
import { getUserRepository } from '../../../libs/repositories/user';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { Logger } from '../../../libs/services/logger.service';
import { loadConfigurationManagerConfig } from '../../../modules/configuration_manager/config/config';
import { userAdminCheck } from '../../../modules/user_management/middlewares/userAdminCheck';
import { isUserOrgAdmin } from '../../../modules/user_management/services/user-admin.service';
import type { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import {
  licenseNotice,
  startupWarnings,
  upgradeGraceEndsAt,
} from './license-notice';
import {
  checkSeats,
  SEAT_REFUSAL_AT_SIGN_IN,
  seatRefusal,
  seatRoom,
  SERVICE_ACCOUNTS_HOLD_SEATS,
  type SeatLimit,
} from './license-seats';
import {
  installLicense,
  licenseStatus,
  publishPermissionSync,
  type LicenseStatusView,
  type LicenseStore,
} from './license.service';

/**
 * How often the license is checked again. Expiry and grace end flip the
 * connectors' answer within this time, without a restart; the connectors read
 * it at most every 5 minutes as well.
 */
export const LICENSE_CHECK_INTERVAL_MS = 5 * 60 * 1000;

/** Values are encrypted like every other `/services` key, so the Python store can read them. */
export function encryptedLicenseStore(
  kv: Pick<KeyValueStoreService, 'get' | 'set'>,
  encryption: Pick<EncryptionService, 'encrypt' | 'decrypt'>,
): LicenseStore {
  return {
    async read(key) {
      const stored = await kv.get<string>(key);
      return stored === null || stored === ''
        ? null
        : (JSON.parse(encryption.decrypt(stored)) as unknown);
    },
    write: (key, value) =>
      kv.set(key, encryption.encrypt(JSON.stringify(value))),
  };
}

let store: LicenseStore | null = null;
const configuredStore = (): LicenseStore => {
  if (!store) {
    const config = loadConfigurationManagerConfig();
    store = encryptedLicenseStore(
      KeyValueStoreService.getInstance(config),
      EncryptionService.getInstance(config.algorithm, config.secretKey),
    );
  }
  return store;
};
/** Replaces the configured store (tests); null opens the configured one again on next use. */
export const setLicenseStore = (replacement: LicenseStore | null): void => {
  store = replacement;
};
/** Opens the configured store on first use, so a store that cannot open fails one check, not start-up. */
const defaultStore: LicenseStore = {
  read: (key) => configuredStore().read(key),
  write: (key, value) => configuredStore().write(key, value),
};

const logger = Logger.getInstance({ service: 'License' });

/** The people holding seats, counted in the oldest organization: an install has one, and a second cannot be created. */
async function countInstallPeople(): Promise<number> {
  const orgId = await getIdentityPort().oldestOrgId();
  return orgId === null
    ? 0
    : inInstallOrg(
        'licence-seats',
        () => getUserRepository().countLive(orgId, SERVICE_ACCOUNTS_HOLD_SEATS),
        0,
      );
}

/** The seat state of the install, stored as it changes. */
export const installSeats = (
  licenseStore: LicenseStore = defaultStore,
  now: Date = new Date(),
): Promise<SeatLimit | null> =>
  checkSeats(licenseStore, countInstallPeople, now);

/**
 * The licence's seat limit, for the open code's `SeatGuard` seam.
 * shortcut: checked before the write, not with it, so two adds at once can both pass; the next check records the true count.
 */
export const licenceSeatGuard: SeatGuard = {
  async check() {
    const limit = await installSeats();
    return {
      room: seatRoom(limit),
      refusal: {
        admin: limit ? seatRefusal(limit.seats) : '',
        signIn: SEAT_REFUSAL_AT_SIGN_IN,
      },
    };
  },
  // Stores the new seat state after people were added or removed; the periodic check repairs a failure.
  async afterChange() {
    try {
      await installSeats();
    } catch (error) {
      logger.warn('Could not update the seat count after a user change', {
        error: error instanceof Error ? error.message : String(error),
      });
    }
  },
};

export type LicenseStatusWithSeats = LicenseStatusView & {
  /** Null when nothing limits seats: no license in force, or `unlimited` seats. */
  seatLimit: SeatLimit | null;
};

const withSeats = async (
  licenseStore: LicenseStore,
  status: LicenseStatusView,
): Promise<LicenseStatusWithSeats> => ({
  ...status,
  seatLimit: await installSeats(licenseStore),
});

/** The seat state for the admins' banner; null when it cannot be worked out, so the other notices still show. */
async function noticeSeats(
  licenseStore: LicenseStore,
): Promise<SeatLimit | null> {
  try {
    return await installSeats(licenseStore);
  } catch (error) {
    logger.warn('Could not work out the seat state for the licence notices', {
      error: error instanceof Error ? error.message : String(error),
    });
    return null;
  }
}

/** Only an admin reads or installs the license; an OAuth app the admin authorised for something else is refused. */
export function createLicenseRouter(
  container: Container,
  licenseStore: LicenseStore = defaultStore,
): Router {
  const router = Router();
  // Resolved per request, so mounting needs nothing from the container yet.
  const authenticate: RequestHandler = (req, res, next) =>
    void container
      .get<AuthMiddleware>('AuthMiddleware')
      .authenticate(req, res, next);
  const admin = [
    authenticate,
    requireScopes(OAuthScopeNames.ORG_ADMIN),
    userAdminCheck,
  ];
  // No sign-in: the procurement site asks the install, server to server, whether its upgrade grace runs.
  // Only the end of the grace (null when licensed, not started or ended); nothing about the licence or the org.
  router.get(
    '/grace',
    async (_req: Request, res: Response, next: NextFunction) => {
      try {
        const now = new Date();
        res.json({
          graceEndsAt: upgradeGraceEndsAt(
            await licenseStatus(licenseStore, now),
            now,
          ),
        });
      } catch (error) {
        next(error);
      }
    },
  );
  router.get(
    '/',
    ...admin,
    async (_req: Request, res: Response, next: NextFunction) => {
      try {
        res.json(
          await withSeats(licenseStore, await licenseStatus(licenseStore)),
        );
      } catch (error) {
        next(error);
      }
    },
  );
  // Any signed-in user: what the notices need, without the file, its signature or the licensee.
  router.get(
    '/notice',
    authenticate,
    requireScopes(OAuthScopeNames.ORG_READ),
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const user = (req as AuthenticatedUserRequest).user as
          { userId?: unknown; orgId?: unknown } | undefined;
        const userId = typeof user?.userId === 'string' ? user.userId : '';
        const orgId = typeof user?.orgId === 'string' ? user.orgId : '';
        if (userId === '' || orgId === '') {
          throw new NotFoundError('Account not found');
        }
        const isAdmin = await isUserOrgAdmin(userId, orgId);
        res.json({
          ...(await licenseNotice(licenseStore, { userId, orgId, isAdmin })),
          // Admins only: the seat banner. A member never gets the count.
          seatLimit: isAdmin ? await noticeSeats(licenseStore) : null,
        });
      } catch (error) {
        next(error);
      }
    },
  );
  // Body: { license: <the license file's JSON> }. Installs it, or replaces the one in force with a newer one.
  router.post(
    '/',
    ...admin,
    async (req: Request, res: Response, next: NextFunction) => {
      try {
        const result = await installLicense(
          licenseStore,
          (req.body as { license?: unknown } | undefined)?.license,
        );
        if (result.installed) {
          logger.info('License installed', {
            licenseId: result.status.license?.licenseId,
            status: result.status.status,
            permissionSync: result.status.permissionSync,
          });
        }
        res.json({
          ...result,
          status: await withSeats(licenseStore, result.status),
        });
      } catch (error) {
        next(error);
      }
    },
  );
  return router;
}

let timer: NodeJS.Timeout | null = null;
let lastAllowed: boolean | undefined;
let warnedAtStart = false;
let lastRefusing: boolean | undefined;

async function checkLicense(licenseStore: LicenseStore): Promise<void> {
  try {
    const answer = await publishPermissionSync(licenseStore);
    if (!warnedAtStart) {
      warnedAtStart = true;
      const now = new Date();
      for (const warning of startupWarnings(
        await licenseStatus(licenseStore, now),
        now,
      )) {
        logger.warn(warning);
      }
    }
    if (answer && answer.allowed !== lastAllowed) {
      if (answer.allowed) {
        logger.info('A license key allows permission sync', { ...answer });
      } else {
        logger.warn(
          'No license key in force allows permission sync: connectors stop copying access from their sources',
          { status: answer.status },
        );
      }
    }
    lastAllowed = answer?.allowed;
    const seats = await installSeats(licenseStore);
    if (seats?.refusing && !lastRefusing) {
      logger.warn('More users than the licence covers: new users are refused', {
        seats: seats.seats,
        used: seats.used,
      });
    }
    lastRefusing = seats?.refusing;
  } catch (error) {
    logger.error('License check failed', {
      error: error instanceof Error ? error.message : String(error),
    });
  }
}

/** Checks at start-up and then every LICENSE_CHECK_INTERVAL_MS. */
export function startLicenseCheck(
  licenseStore: LicenseStore = defaultStore,
): void {
  if (timer) return;
  const run = (): void => void checkLicense(licenseStore);
  timer = spawnDetached(() => {
    run();
    return setInterval(run, LICENSE_CHECK_INTERVAL_MS);
  });
  timer.unref();
}

export function stopLicenseCheck(): void {
  if (timer) clearInterval(timer);
  timer = null;
}

/** The open-source build's start-up hooks: the license routes and the periodic check. */
export const licenseEditionBoot: EditionBoot = {
  ...noopEditionBoot,
  seatGuard: licenceSeatGuard,
  mountRoutes: (app, containers) => {
    if (containers.entityManager) {
      app.use('/api/v1/license', createLicenseRouter(containers.entityManager));
    }
  },
  startWorkers: () => {
    startLicenseCheck();
  },
  stop: () => {
    stopLicenseCheck();
    return Promise.resolve();
  },
};
