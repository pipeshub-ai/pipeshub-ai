import type { OrgDeletedAnnouncer } from '../../../../modules/agent-harness/ports/principal-erasure';
import { pickHarnessStore } from '../stores';
import { OutboxOrgDeletedAnnouncer } from './mongo';
import { PgOrgDeletedAnnouncer } from './pg';

export const createOrgDeletedAnnouncer = (): OrgDeletedAnnouncer =>
  pickHarnessStore<OrgDeletedAnnouncer>('agent-harness/org-deleted-announcer', {
    mongo: () => new OutboxOrgDeletedAnnouncer(),
    postgres: () => new PgOrgDeletedAnnouncer(),
  });
