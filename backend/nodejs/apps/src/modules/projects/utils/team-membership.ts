import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { callerIdentityOf } from '../../../libs/types/caller-identity';
import { AppConfig } from '../../tokens_manager/config/config';
import { teamDirectoryFor } from '../../user_management/services/team-directory.service';

const logger = Logger.getInstance({ service: 'ProjectTeamMembership' });

/**
 * Org/team graph ids of every team the requesting user belongs to — the
 * membership check `ProjectService.computeRole` needs. Never throws: a failed
 * lookup degrades to "caller belongs to no teams" rather than failing the
 * project request.
 */
export async function resolveCallerTeamIds(
  req: AuthenticatedUserRequest,
  appConfig: AppConfig,
): Promise<string[]> {
  const result = await teamDirectoryFor(appConfig, logger).callerTeamIds(
    callerIdentityOf(req),
  );
  return result.status === 'ok' ? [...result.teamIds] : [];
}
