import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { configPaths } from '../../paths/paths';
import { OrgAuthConfig } from '../../../auth/schema/orgAuthConfiguration.schema';
import { withoutRemovedSignInMethods } from '../../../auth/utils/removedSignInMethods';
import { RENAMED_SIGN_IN_METHODS } from '../../../../edition';

const MIGRATION_FLAG_DONE = 'true';

export interface SignInMethodsRemovalResult {
  policiesUpdated: number;
  azureAdConfigCopied: boolean;
  errored: number;
}

/**
 * One-time rewrite for 1.0: takes the sign-in methods this edition removed
 * out of every policy (falling back to password), moves Azure AD sign-in to
 * Microsoft, and records what was removed so admins can be told. Safe to re-run.
 */
export class SignInMethodsRemovalMigration {
  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
  ) {}

  async run(): Promise<SignInMethodsRemovalResult> {
    const result: SignInMethodsRemovalResult = {
      policiesUpdated: 0,
      azureAdConfigCopied: false,
      errored: 0,
    };
    try {
      const flag = await this.kvStore.get<string>(
        configPaths.signInMethodsRemovalMigration,
      );
      if (flag === MIGRATION_FLAG_DONE) {
        this.logger.info('Sign-in methods removal already completed; skipping');
        return result;
      }
    } catch (error) {
      this.logger.warn(
        'Failed to read sign-in methods removal flag; proceeding with idempotent run',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
    }

    await this.copyAzureAdConfig(result);
    // A policy turned to Microsoft before its settings were copied could not
    // sign anyone in; leave both for the next boot to retry.
    if (result.errored === 0) {
      await this.rewritePolicies(result);
    }

    if (result.errored === 0) {
      try {
        await this.kvStore.set<string>(
          configPaths.signInMethodsRemovalMigration,
          MIGRATION_FLAG_DONE,
        );
      } catch (error) {
        result.errored += 1;
        this.logger.error('Failed to record sign-in methods removal as done', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      }
    }
    return result;
  }

  // Both settings hold the same encrypted { clientId, tenantId } value, so the
  // stored string is copied as it is. Existing Microsoft settings win.
  private async copyAzureAdConfig(
    result: SignInMethodsRemovalResult,
  ): Promise<void> {
    if (RENAMED_SIGN_IN_METHODS.azureAd !== 'microsoft') return;
    try {
      const azureAd = await this.kvStore.get<string>(configPaths.auth.azureAD);
      if (!azureAd) return;
      const microsoft = await this.kvStore.get<string>(
        configPaths.auth.microsoft,
      );
      if (microsoft) return;
      await this.kvStore.set<string>(configPaths.auth.microsoft, azureAd);
      result.azureAdConfigCopied = true;
    } catch (error) {
      result.errored += 1;
      this.logger.error('Failed to copy Azure AD settings to Microsoft', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  private async rewritePolicies(
    result: SignInMethodsRemovalResult,
  ): Promise<void> {
    let policies: {
      _id: unknown;
      orgId: unknown;
      authSteps?: { order: number; allowedMethods: { type: string }[] }[];
      removedSignInMethods?: string[];
    }[];
    try {
      policies = await OrgAuthConfig.find({ isDeleted: { $ne: true } })
        .select('_id orgId authSteps removedSignInMethods')
        .lean();
    } catch (error) {
      result.errored += 1;
      this.logger.error('Failed to read sign-in policies', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return;
    }

    for (const policy of policies) {
      const { steps, removed, changed } = withoutRemovedSignInMethods(
        policy.authSteps,
      );
      if (!changed) continue;
      const removedSignInMethods = [
        ...new Set([...(policy.removedSignInMethods ?? []), ...removed]),
      ];
      try {
        await OrgAuthConfig.updateOne(
          { _id: policy._id },
          {
            $set: {
              authSteps: steps,
              ...(removedSignInMethods.length > 0
                ? { removedSignInMethods }
                : {}),
            },
          },
        );
        result.policiesUpdated += 1;
        this.logger.info('Removed sign-in methods from policy', {
          orgId: String(policy.orgId),
          removed,
        });
      } catch (error) {
        result.errored += 1;
        this.logger.error('Failed to rewrite sign-in policy', {
          orgId: String(policy.orgId),
          error: error instanceof Error ? error.message : 'Unknown error',
        });
      }
    }
  }
}
