// Prints a single-use link that lets one person choose a password, for
// servers that cannot send email. Run it where the backend runs:
//   node dist/scripts/set-password-link.js person@example.com
import '../redisProviders';
import {
  closeAllRedisProviders,
  getPreparedRedisProvider,
} from '../libs/services/redis/connectionProviderFactory';
import { MongoService } from '../libs/services/mongo.service';
import { initDocStore } from '../libs/repositories/doc-store';
import { inInstallOrg } from '../libs/repositories/identity/install-org';
import { usesMongo } from '../libs/repositories/store-select';
import { getUserRepository } from '../libs/repositories/user';
import {
  jwtGeneratorForForgotPasswordLink,
  passwordResetLinkLifetime,
} from '../libs/utils/createJwt';
import { loadAppConfig } from '../modules/tokens_manager/config/config';

export interface SetPasswordLink {
  link: string;
  /** How long the link works, e.g. "20 minutes". */
  lifetime: string;
}

interface LinkUser {
  _id: unknown;
  orgId: unknown;
  email: string;
}

export type FindLinkUser = (email: string) => Promise<LinkUser | null>;

// The oldest live account that uses the address.
const findActiveUser: FindLinkUser = async (email) => {
  return inInstallOrg(
    'set-password-link',
    () => getUserRepository().findActiveByEmail(email),
    null,
  );
};

/** Builds the same link a "Forgot password" email carries. */
export async function createSetPasswordLink(
  email: string,
  config: { frontendUrl: string; scopedJwtSecret: string },
  findUser: FindLinkUser = findActiveUser,
): Promise<SetPasswordLink> {
  const address = email.trim().toLowerCase();
  const user = await findUser(address);
  if (!user) {
    throw new Error(
      `No active PipesHub account uses ${address}. Check the address and try again.`,
    );
  }
  const { passwordResetToken } = jwtGeneratorForForgotPasswordLink(
    user.email,
    String(user._id),
    String(user.orgId),
    config.scopedJwtSecret,
  );
  return {
    link: `${config.frontendUrl}/reset-password#token=${passwordResetToken}`,
    lifetime: passwordResetLinkLifetime().description,
  };
}

export const USAGE = 'Usage: node dist/scripts/set-password-link.js <email>';

/** Returns the process exit code. */
export async function main(
  args: string[],
  out: (line: string) => void = console.log,
  err: (line: string) => void = console.error,
): Promise<number> {
  const [email] = args;
  if (!email || args.length > 1) {
    err(USAGE);
    return 2;
  }
  await getPreparedRedisProvider();
  const appConfig = await loadAppConfig();
  const mongo = usesMongo() ? new MongoService(appConfig.mongo) : undefined;
  await mongo?.initialize();
  await initDocStore(process.env.APP_VERSION ?? 'dev', { jobs: false });
  try {
    const { link, lifetime } = await createSetPasswordLink(email, appConfig);
    out(
      `Send this link to ${email.trim().toLowerCase()}. It works once, for ${lifetime}, and lets them choose a password:`,
    );
    out(link);
    return 0;
  } catch (error) {
    err(error instanceof Error ? error.message : String(error));
    return 1;
  } finally {
    await mongo?.destroy();
    await closeAllRedisProviders();
  }
}

if (require.main === module) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (error: unknown) => {
      console.error(error instanceof Error ? error.message : String(error));
      process.exit(1);
    },
  );
}
