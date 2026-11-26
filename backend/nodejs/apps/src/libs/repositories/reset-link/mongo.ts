import { UsedPasswordResetLink } from '../../../modules/auth/schema/usedPasswordResetLink.schema';
import { isDuplicateKeyError } from '../../utils/mongo.utils';
import { ResetLinkStoreNotReadyError } from './types';
import type { ResetLinkRepository } from './types';

export class MongoResetLinkRepository implements ResetLinkRepository {
  async claim(input: {
    linkHash: string;
    userId: string;
    orgId: string;
    expiresAt: Date;
  }): Promise<boolean> {
    try {
      // Mongoose builds indexes in the background; without the unique index
      // both inserts would succeed, so a link could be used twice.
      await UsedPasswordResetLink.init();
    } catch (initError) {
      // init() keeps its first rejection for the life of the process, so a
      // failed build is retried here rather than refusing every later reset.
      try {
        await UsedPasswordResetLink.ensureIndexes();
      } catch (error) {
        throw new ResetLinkStoreNotReadyError(
          `${error instanceof Error ? error.message : String(error)} (first: ${
            initError instanceof Error ? initError.message : String(initError)
          })`,
        );
      }
    }

    try {
      await UsedPasswordResetLink.create(input);
    } catch (error) {
      if (isDuplicateKeyError(error)) return false;
      throw error;
    }
    return true;
  }

  async release(linkHash: string): Promise<void> {
    await UsedPasswordResetLink.deleteOne({ linkHash });
  }
}
