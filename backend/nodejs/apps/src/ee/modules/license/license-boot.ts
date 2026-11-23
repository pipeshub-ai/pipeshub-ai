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
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import type { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { Logger } from '../../../libs/services/logger.service';
import { loadConfigurationManagerConfig } from '../../../modules/configuration_manager/config/config';
import { userAdminCheck } from '../../../modules/user_management/middlewares/userAdminCheck';
import { isUserOrgAdmin } from '../../../modules/user_management/services/user-admin.service';
import type { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { licenseNotice, startupWarnings } from './license-notice';
import {
  installLicense,
  licenseStatus,
  publishPermissionSync,
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
/** Opens the configured store on first use, so a store that cannot open fails one check, not start-up. */
const defaultStore: LicenseStore = {
  read: (key) => configuredStore().read(key),
  write: (key, value) => configuredStore().write(key, value),
};

const logger = Logger.getInstance({ service: 'License' });

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
  router.get(
    '/',
    ...admin,
    async (_req: Request, res: Response, next: NextFunction) => {
      try {
        res.json(await licenseStatus(licenseStore));
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
        res.json(
          await licenseNotice(licenseStore, {
            userId,
            orgId,
            isAdmin: await isUserOrgAdmin(userId, orgId),
          }),
        );
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
        res.json(result);
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
