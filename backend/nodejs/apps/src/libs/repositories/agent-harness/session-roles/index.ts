import type { SessionRoleLookup } from '../../../../modules/agent-harness/ports/session-roles';
import { pickHarnessStore } from '../stores';
import { HarnessSessionRoles } from './mongo';
import { PgHarnessSessionRoles } from './pg';

export const createSessionRoles = (): SessionRoleLookup =>
  pickHarnessStore<SessionRoleLookup>('agent-harness/session-roles', {
    mongo: () => new HarnessSessionRoles(),
    postgres: () => new PgHarnessSessionRoles(),
  });
