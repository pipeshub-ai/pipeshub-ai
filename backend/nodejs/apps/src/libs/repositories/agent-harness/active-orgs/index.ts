import { pickHarnessStore } from '../stores';
import { activeOrgIds as mongoActiveOrgIds } from './mongo';
import { pgActiveOrgIds } from './pg';

/** Every org not deleted, from the selected document store. */
export const activeOrgIds = (): Promise<string[]> =>
  pickHarnessStore<() => Promise<string[]>>('agent-harness/active-orgs', {
    mongo: () => mongoActiveOrgIds,
    postgres: () => pgActiveOrgIds,
  })();
