import type { InboxStore } from '../../../../modules/agent-harness/ports/inbox';
import { pickHarnessStore } from '../stores';
import { MongoInboxStore } from './mongo';
import { PgInboxStore } from './pg';

export { RepositoryInboxNotices } from './notices';

/** A person's Inbox settings, run-notice claims and watch leases, on the selected document store. */
export const createInboxStore = (): InboxStore =>
  pickHarnessStore<InboxStore>('agent-harness/inbox', {
    mongo: () => new MongoInboxStore(),
    postgres: () => new PgInboxStore(),
  });
