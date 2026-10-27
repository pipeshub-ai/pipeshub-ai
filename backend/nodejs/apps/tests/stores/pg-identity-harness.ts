/**
 * Helpers for running the identity contract suites on PostgreSQL. The suites call repositories with no organisation
 * context; a unit of work runs as an organisation and a user, so each call is wrapped to run as the organisation its
 * arguments name and as the user the call needs: the account itself for its own credentials, an administrator of the
 * organisation for the administrator effects, a member for the questions that prove membership.
 */
import { randomUUID } from 'node:crypto';
import { OrgLedger, PgStack, inOrg } from './pg-store-harness';
import { newObjectId } from '../../src/libs/repositories/pg-util';

type Who = 'admin' | 'anyone' | { arg: number } | ((args: unknown[]) => string);
export interface CallSpec {
  /** Positional index of the organisation argument (otherwise learned from the arguments). */
  org?: number;
  as?: Who;
  /** Returned as it is (a synchronous call). */
  raw?: true;
  /** Opens its own unit with no organisation (or, for an activity, may name none): called with no context. */
  pre?: true;
  /** The call may name no organisation (an activity of a person not yet known to one). */
  maybePre?: true;
}
export type Spec = Record<string, CallSpec>;

export class IdentityWorld {
  private readonly admins = new Map<string, string>();
  /** `stack` is set once the cluster is up; the suites build their repositories before that. */
  constructor(public stack: PgStack, readonly ledger: OrgLedger) {}

  /** An organisation row, written outside every policy. */
  async newOrg(): Promise<string> {
    const org = newObjectId();
    const now = new Date();
    await this.stack.admin((c) =>
      c.query(
        `INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, registered_name, created_at, updated_at)
          VALUES ($1, $2, $3, 'business', 'Contract Org', $4, $4)`,
        [org, `${randomUUID().slice(0, 8)}.example.com`, `owner-${randomUUID().slice(0, 8)}@example.com`, now],
      ),
    );
    this.ledger.learn({ orgId: org });
    return org;
  }

  /** A live administrator of the organisation (made once), the user the administrator effects run as. */
  async adminOf(org: string): Promise<string> {
    const known = this.admins.get(org);
    if (known) return known;
    const id = newObjectId();
    const now = new Date();
    await this.stack.admin((c) =>
      c.query(
        `INSERT INTO identity.users (org_id, id, slug, full_name, email, role, created_at, updated_at) VALUES ($1, $2, $3, 'Org Admin', $4, 'admin', $5, $5)`,
        [org, id, `user-admin-${id}`, `admin-${id}@example.com`, now],
      ),
    );
    this.admins.set(org, id);
    return id;
  }

  /** A live account of the organisation (the first one), else an administrator is made: the user for questions that only need a member. */
  async memberOf(org: string): Promise<string> {
    const r = await this.stack.admin((c) => c.query('SELECT id FROM identity.users WHERE org_id = $1 AND NOT is_deleted ORDER BY created_at, id LIMIT 1', [org]));
    return (r.rows[0]?.id as string | undefined) ?? 'u1';
  }

  /** Every organisation row and what hangs off it, gone (the single-organisation lookups are install-wide). */
  async clearOrgs(): Promise<void> {
    await this.stack.admin((c) => c.query('TRUNCATE identity.orgs, identity.org_auth_config'));
    this.admins.clear();
  }

  /** A repository whose methods run as the organisation their arguments name, as the user `spec` says. */
  scoped<T extends object>(repo: T, spec: Spec = {}): T {
    return new Proxy(repo, {
      get: (target, prop, receiver) => {
        const v = Reflect.get(target, prop, receiver) as unknown;
        if (typeof v !== 'function' || typeof prop !== 'string') return v;
        const call = (v as (...a: unknown[]) => unknown).bind(target);
        if (spec[prop]?.raw) return call;
        return async (...args: unknown[]) => {
          const s = spec[prop] ?? {};
          if (s.pre) {
            const out = await call(...args);
            this.ledger.learn(out);
            return out;
          }
          let org: string;
          try {
            org = this.ledger.orgFor(args, s.org);
          } catch (e) {
            if (!s.maybePre) throw e;
            return call(...args);
          }
          this.ledger.learn(args);
          let user: string | undefined;
          if (s.as === 'admin') user = await this.adminOf(org);
          else if (s.as === 'anyone') user = await this.memberOf(org);
          else if (typeof s.as === 'function') user = s.as(args);
          else if (s.as) user = args[s.as.arg] as string;
          const out = await inOrg(org, () => call(...args) as Promise<unknown>, user);
          this.ledger.learn(out);
          return out;
        };
      },
    });
  }
}

const self = { arg: 0 };
const first = (names: string[], extra: CallSpec = {}): Spec => Object.fromEntries(names.map((n) => [n, { org: 0, ...extra }]));
const ofObject = (args: unknown[]): string => (args[0] as { userId: string }).userId;
/** Which user each credential call needs, and where its organisation argument sits. */
export const IDENTITY_SPEC: Spec = {
  loginCandidates: { pre: true },
  oldestOrgId: { pre: true },
  orgCount: { pre: true },
  recordPreOrgActivity: { pre: true },
  find: { org: 1, as: self },
  clearBlock: { org: 1, as: self },
  setBlocked: { org: 1, as: self },
  reserveAttempt: { org: 1, as: self },
  releaseAttempt: { org: 1, as: self },
  incrementWrongCount: { org: 1, as: self },
  claimOtp: { org: 1, as: self },
  saveOtp: { org: 1, as: self },
  savePassword: { org: 1, as: self },
  createWithPassword: { as: ofObject },
  touchOnRefresh: { org: 1, as: self },
  unblock: { org: 1, as: 'admin' },
  blockedUserIds: { org: 0, as: 'anyone' },
  clearPassword: { as: 'admin' },
  deleteForUser: { as: 'admin' },
  discardForUser: { as: 'admin' },
};
export const USER_SPEC: Spec = {
  ...first([
    'getLive', 'getForEdit', 'getEmail', 'getAny', 'getAuthSnapshot', 'getRoleSnapshot', 'getLiveFields', 'getByIdOrEmail', 'getOldestId', 'getLiveByEmail',
    'listLiveByIds', 'listNamesByIds', 'listAllLive', 'listSummariesByIds', 'listWithGroups', 'listByEmails', 'listAdminIds', 'listServiceAccounts',
    'getServiceAccount', 'serviceAccountIdsAmong', 'countActiveAdmins', 'emailTakenByOther', 'hasRemovedAccount', 'create', 'createMany', 'updateProfile', 'markDeleted',
    'restoreByEmails', 'promoteToAdmin', 'setRole', 'lockOrgAdminChanges', 'deleteRemoved', 'listRemovedIdsByEmail', 'setDisabledByEmailDomain',
    'restoreServiceAccount', 'enableServiceAccount', 'updateServiceAccount',
  ]),
  listDirectory: { org: 0, as: 'anyone' },
  changeEmail: { as: self },
  correctEmail: { as: self },
};
export const GROUP_SPEC: Spec = first([
  'list', 'get', 'getLive', 'nameTaken', 'create', 'rename', 'softDelete', 'addMembers', 'removeMembers', 'addMemberToGroups', 'addToEveryone',
  'removeFromEveryone', 'removeFromAll', 'groupsOfUser', 'memberIdsOfTypes', 'summariesForUsers', 'stats',
]);
export const ACTIVITY_SPEC: Spec = {
  record: { maybePre: true },
  recordMany: { maybePre: true },
  latestSessionEnding: { org: 1 },
  latestPasswordChange: { org: 1 },
};
export const AUTH_CONFIG_SPEC: Spec = {
  ...first(['get', 'getLive', 'allowsPassword', 'removedSignInMethods', 'createDefault', 'replaceSteps']),
  getFirstLive: { pre: true },
  anyLive: { pre: true },
};
export const PICTURE_SPEC: Spec = first(['get', 'set', 'remove', 'listSet']);
export const ORG_SPEC: Spec = {
  ...first(['getLive', 'isActive', 'getAccountType', 'getNames', 'getOnboardingStatus', 'update', 'setOnboardingStatus', 'softDelete', 'getLogo', 'setLogo', 'removeLogo']),
  count: { pre: true },
  getFirstLive: { pre: true },
  liveSummary: { pre: true },
  allocateIds: { raw: true },
  createWithAdmin: { pre: true },
};
