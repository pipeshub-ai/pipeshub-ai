/**
 * Main's identity rows for the harness stores that read them on PostgreSQL (engine sign-ins, coding-engine choices,
 * approvers, schedule notices): orgs and users written outside every policy, and main's PostgreSQL
 * repositories installed for the suite, whatever `DOC_STORE` the test process has.
 */
import { randomUUID } from 'node:crypto';
import { setConversationRepository } from '../../../../src/libs/repositories/conversation';
import { PgConversationRepository } from '../../../../src/libs/repositories/conversation/pg';
import { setGroupRepository } from '../../../../src/libs/repositories/group';
import { PgGroupRepository } from '../../../../src/libs/repositories/group/pg';
import { setNotificationRepository } from '../../../../src/libs/repositories/notification';
import { PgNotificationRepository } from '../../../../src/libs/repositories/notification/pg';
import { setOrgRepository } from '../../../../src/libs/repositories/org';
import { PgOrgRepository } from '../../../../src/libs/repositories/org/pg';
import { setUserRepository } from '../../../../src/libs/repositories/user';
import { PgUserRepository } from '../../../../src/libs/repositories/user/pg';
import { newObjectId } from '../../../../src/libs/repositories/pg-util';
import type { PgStack } from './harness-pg';

/** Main's repositories on PostgreSQL for this suite; the returned function puts back the defaults. */
export function usePgIdentityRepositories(): () => void {
  setUserRepository(new PgUserRepository());
  setOrgRepository(new PgOrgRepository());
  setGroupRepository(new PgGroupRepository());
  setConversationRepository(new PgConversationRepository());
  setNotificationRepository(new PgNotificationRepository());
  return () => {
    setUserRepository();
    setOrgRepository();
    setGroupRepository();
    setConversationRepository();
    setNotificationRepository();
  };
}

export async function pgOrg(
  stack: PgStack,
  opts: { deleted?: boolean } = {},
): Promise<string> {
  const org = newObjectId();
  const now = new Date();
  await stack.admin((c) =>
    c.query(
      `INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, registered_name, is_deleted, created_at, updated_at)
        VALUES ($1, $2, $3, 'business', 'Harness Org', $4, $5, $5)`,
      [
        org,
        `${randomUUID().slice(0, 8)}.example.com`,
        `owner-${randomUUID().slice(0, 8)}@example.com`,
        opts.deleted === true,
        now,
      ],
    ),
  );
  return org;
}

export interface PgUserOptions {
  kind?: 'human' | 'service';
  role?: 'admin' | 'member';
  deleted?: boolean;
  fullName?: string;
  email?: string;
}

export async function pgUser(
  stack: PgStack,
  orgId: string,
  opts: PgUserOptions = {},
): Promise<string> {
  const id = newObjectId();
  const now = new Date();
  await stack.admin((c) =>
    c.query(
      `INSERT INTO identity.users (org_id, id, slug, full_name, email, kind, role, is_deleted, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9)`,
      [
        orgId,
        id,
        `user-${id}`,
        opts.fullName ?? null,
        opts.email ?? `${id}@example.com`,
        opts.kind ?? 'human',
        opts.role ?? 'member',
        opts.deleted === true,
        now,
      ],
    ),
  );
  return id;
}

export async function setUserDeleted(
  stack: PgStack,
  orgId: string,
  userId: string,
): Promise<void> {
  await stack.admin((c) =>
    c.query(
      `UPDATE identity.users SET is_deleted = true WHERE org_id = $1 AND id = $2`,
      [orgId, userId],
    ),
  );
}
