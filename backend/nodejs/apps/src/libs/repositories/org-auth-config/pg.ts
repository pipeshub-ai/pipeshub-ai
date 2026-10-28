/**
 * PostgreSQL implementation of the sign-in configuration repository (`DOC_STORE=postgres`): one row per organisation holding
 * its ordered steps. The install-wide questions of the single-organisation sign-in pages (the first configuration, whether any
 * exists) ask the identity port which organisation is oldest and read that organisation's row.
 */
import { ValidationError } from '../../errors/validation.error';
import {
  currentOrgContextOrSystem,
  makeOrgContext,
  runWithOrgContext,
} from '../../context/orgContext';
import { AuthMethodType } from '../../../modules/auth/types/auth-method';
import { getIdentityPort } from '../identity';
import { assertOrg, inUnit, newObjectId } from '../pg-util';
import type { Tx } from '../uow';
import type {
  AuthStepDto,
  OrgAuthConfigDto,
  OrgAuthConfigRepository,
} from './types';

interface ConfigRow {
  id: string;
  org_id: string;
  domain: string | null;
  auth_steps: AuthStepDto[];
  is_deleted: boolean;
  version: number;
  created_at: Date;
  updated_at: Date;
}

const COLS =
  'c.id, c.org_id, c.domain, c.auth_steps, c.is_deleted, c.version, c.created_at, c.updated_at';
const METHODS: ReadonlySet<string> = new Set(Object.values(AuthMethodType));

const dto = (r: ConfigRow): OrgAuthConfigDto => ({
  _id: r.id,
  orgId: r.org_id,
  ...(r.domain !== null && { domain: r.domain }),
  authSteps: r.auth_steps,
  isDeleted: r.is_deleted,
  createdAt: r.created_at,
  updatedAt: r.updated_at,
  __v: r.version,
});

/** What the document schema enforced: one to three steps with distinct orders, each method one of the known types. */
function checkedSteps(steps: AuthStepDto[]): AuthStepDto[] {
  if (steps.length < 1 || steps.length > 3)
    throw new ValidationError(
      'Must have between 1 and 3 authentication steps',
      [],
    );
  if (new Set(steps.map((s) => s.order)).size !== steps.length)
    throw new ValidationError('Each step must have a unique order', []);
  return steps.map((s) => ({
    order: s.order,
    allowedMethods: s.allowedMethods.map((m) => {
      if (!METHODS.has(m.type))
        throw new ValidationError(
          `unknown authentication method ${m.type}`,
          [],
        );
      return { type: m.type };
    }),
  }));
}

export class PgOrgAuthConfigRepository implements OrgAuthConfigRepository {
  private one(orgId: string, live: boolean): Promise<OrgAuthConfigDto | null> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<ConfigRow>(
        `/* pipeshub:identity.auth_config_get */ SELECT ${COLS} FROM identity.org_auth_config c WHERE c.org_id = $1${live ? ' AND NOT c.is_deleted' : ''}`,
        [k.orgId],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }

  get(orgId: string): Promise<OrgAuthConfigDto | null> {
    return this.one(orgId, false);
  }

  getLive(orgId: string): Promise<OrgAuthConfigDto | null> {
    return this.one(orgId, true);
  }

  async getFirstLive(): Promise<OrgAuthConfigDto | null> {
    const id = await getIdentityPort().oldestOrgId();
    if (id === null) return null;
    const outer = (() => {
      try {
        return currentOrgContextOrSystem();
      } catch {
        return undefined;
      }
    })();
    if (outer?.orgId && outer.orgId !== id) return null;
    return runWithOrgContext(
      makeOrgContext({
        orgId: id,
        userId: null,
        kind: 'job',
        source: 'job:first-org',
      }),
      () => this.getLive(id),
    );
  }

  async anyLive(): Promise<boolean> {
    return (await this.getFirstLive()) !== null;
  }

  async removedSignInMethods(orgId: string): Promise<string[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ removed: string[] }>(
        `/* pipeshub:identity.auth_config_removed */ SELECT c.removed_sign_in_methods AS removed FROM identity.org_auth_config c WHERE c.org_id = $1 AND NOT c.is_deleted`,
        [k.orgId],
      );
      return rows[0]?.removed ?? [];
    });
  }

  async allowsPassword(orgId: string): Promise<boolean> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query(
        `/* pipeshub:identity.auth_config_allows_password */ SELECT 1 FROM identity.org_auth_config c, jsonb_array_elements(c.auth_steps) s, jsonb_array_elements(s -> 'allowedMethods') m
          WHERE c.org_id = $1 AND m ->> 'type' = 'password' LIMIT 1`,
        [k.orgId],
      );
      return rows.length > 0;
    });
  }

  async createDefault(
    orgId: string,
    domain: string | undefined,
    tx?: Tx,
  ): Promise<void> {
    await inUnit(tx, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const now = new Date();
      await k.query(
        `/* pipeshub:identity.auth_config_create */ INSERT INTO identity.org_auth_config (org_id, id, domain, auth_steps, created_at, updated_at)
          VALUES ($1, $2, $3, '[{"order": 1, "allowedMethods": [{"type": "password"}]}]'::jsonb, $4, $4)`,
        [k.orgId, newObjectId(), domain ?? null, now],
      );
    });
  }

  async replaceSteps(
    orgId: string,
    steps: AuthStepDto[],
  ): Promise<OrgAuthConfigDto | null> {
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const checked = checkedSteps(steps);
      const { rows } = await k.query<ConfigRow>(
        `/* pipeshub:identity.auth_config_replace */ UPDATE identity.org_auth_config c SET auth_steps = $2::jsonb, updated_at = $3 WHERE c.org_id = $1 RETURNING ${COLS}`,
        [k.orgId, JSON.stringify(checked), new Date()],
      );
      return rows[0] ? dto(rows[0]) : null;
    });
  }
}
