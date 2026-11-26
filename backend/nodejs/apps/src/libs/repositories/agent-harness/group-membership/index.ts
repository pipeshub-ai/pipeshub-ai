import type { ActorGroupMembership } from '../../../../modules/agent-harness/ports/group-access';
import { pickHarnessStore } from '../stores';
import { HarnessGroupMembership } from './mongo';
import { PgGroupMembership } from './pg';

/** Membership, the ids an access section may not name, and the names of the groups a page holds (ADR-UI-03). */
export type HarnessGroupDirectory = ActorGroupMembership &
  Pick<PgGroupMembership, 'unknownGroups' | 'groupNames'>;

export const createGroupMembership = (): HarnessGroupDirectory =>
  pickHarnessStore<HarnessGroupDirectory>('agent-harness/group-membership', {
    mongo: () => new HarnessGroupMembership(),
    postgres: () => new PgGroupMembership(),
  });
