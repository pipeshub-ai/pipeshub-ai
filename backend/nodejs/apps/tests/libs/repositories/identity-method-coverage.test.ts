import { expect } from 'chai';
import fs from 'fs';
import path from 'path';
import { MongoUserRepository } from '../../../src/libs/repositories/user/mongo';
import { MongoGroupRepository } from '../../../src/libs/repositories/group/mongo';
import { MongoCredentialRepository } from '../../../src/libs/repositories/credential/mongo';
import { MongoActivityRepository } from '../../../src/libs/repositories/activity/mongo';
import { MongoOrgAuthConfigRepository } from '../../../src/libs/repositories/org-auth-config/mongo';
import { MongoResetLinkRepository } from '../../../src/libs/repositories/reset-link/mongo';
import { MongoPictureRepository } from '../../../src/libs/repositories/picture/mongo';
import { MongoIdentityPort } from '../../../src/libs/repositories/identity/mongo';
import { MongoOrgRepository } from '../../../src/libs/repositories/org/mongo';
import { MongoProjectRepository } from '../../../src/libs/repositories/project/mongo';
import { MongoConnectorsConfigRepository } from '../../../src/libs/repositories/connectors-config/mongo';
import { MongoSlackThreadRepository } from '../../../src/libs/repositories/slack-thread/mongo';
import { MongoDelegationRepository } from '../../../src/libs/repositories/delegation/mongo';
import { MongoOrgMetricsJobRepository } from '../../../src/libs/repositories/org-metrics-job/mongo';

/**
 * Every public method of an identity repository must appear in the contract
 * suite (`users.method(`), so a method added without a contract case fails here.
 */
const SUITE = ['identity.contract.ts', 'rest.contract.ts']
  .map((f) => fs.readFileSync(path.resolve(__dirname, '../../stores/contract', f), 'utf8'))
  .join('\n');

// Methods exercised only through controllers/services, with the reason.
const ALLOWED_UNCOVERED: Record<string, string> = {};

const repos: Array<[string, string, new () => object]> = [
  ['users', 'user', MongoUserRepository],
  ['groups', 'group', MongoGroupRepository],
  ['credentials', 'credential', MongoCredentialRepository],
  ['activities', 'activity', MongoActivityRepository],
  ['orgAuth', 'org-auth-config', MongoOrgAuthConfigRepository],
  ['resetLinks', 'reset-link', MongoResetLinkRepository],
  ['pictures', 'picture', MongoPictureRepository],
  ['identity', 'identity', MongoIdentityPort],
  ['orgs', 'org', MongoOrgRepository],
  ['projects', 'project', MongoProjectRepository],
  ['connectors', 'connectors-config', MongoConnectorsConfigRepository],
  ['slack', 'slack-thread', MongoSlackThreadRepository],
  ['delegation', 'delegation', MongoDelegationRepository],
  ['metrics', 'org-metrics-job', MongoOrgMetricsJobRepository],
];

describe('repositories: every public method has a contract case', () => {
  for (const [handle, dir, Cls] of repos) {
    it(dir, () => {
      const methods = Object.getOwnPropertyNames(Cls.prototype).filter((m) => m !== 'constructor');
      expect(methods.length).to.be.greaterThan(0);
      const missing = methods.filter(
        (m) => !new RegExp(`\\b${handle}\\.${m}\\(`).test(SUITE) && !ALLOWED_UNCOVERED[`${dir}.${m}`],
      );
      expect(missing, `no contract case for ${dir}: ${missing.join(', ')}`).to.deep.equal([]);
    });
  }
});
