import {
  NotFoundError,
  UnauthorizedError,
} from '../../libs/errors/http.errors';
import { getOrgRepository } from '../../libs/repositories/org';

/**
 * The OSS Slack bot configuration carries no orgId, so this edition only has a
 * safe answer when there is exactly one org. On a multi-org instance the old
 * `Org.findOne` picked whichever document came back first, which could answer a
 * Slack workspace with another tenant's data — refuse instead. An edition that
 * serves several orgs installs its own `orgOfBot` (integrations/slack-bot/src/edition.ts).
 */
export const resolveSoleOrgId = async (): Promise<string> => {
  const { count, oldestId } = await getOrgRepository().liveSummary();
  const soleOrg = count === 1 && oldestId !== null ? oldestId : undefined;
  if (soleOrg === undefined) {
    if (count > 1) {
      throw new UnauthorizedError(
        'Slack access is limited to single-organization deployments on this edition',
      );
    }
    throw new NotFoundError('Organization not found');
  }
  return soleOrg;
};
