/**
 * PostgreSQL implementation of the OAuth app repository (`DOC_STORE=postgres`). Every statement names the org first; a call that
 * holds only a client id runs as the install's organisation (see pg-org.ts). The slug counter and the app are one unit, so a
 * create that rolls back gives its number back.
 */
import type { KnowledgeLimit } from '../../../modules/oauth_provider/types/oauth.types';
import slugify from 'slug';
import {
  FIRST_PARTY_DEVICE_CLIENT_ID,
  PAT_APP_CLIENT_ID_PREFIX,
  SERVICE_TOKEN_APP_CLIENT_ID_PREFIX,
} from '../../../modules/oauth_provider/constants/constants';
import { stripNul } from '../nul';
import { Params, likeEscape, newObjectId, oidOf, inUnit } from '../pg-util';
import { clientOrg, goodKey, inKeyUnit, inOrgUnit } from './pg-org';
import {
  OAuthAppStatus,
  type NewOAuthApp,
  type OAuthAppDto,
  type OAuthAppPatch,
  type OAuthAppRepository,
  type OwnedAppListQuery,
  type UpdateOwnedOptions,
} from './types';
import { ConflictError } from '../errors';

interface AppRow {
  id: string;
  slug: string;
  client_id: string;
  client_secret_encrypted: string;
  name: string;
  description: string | null;
  org_id: string;
  created_by: string;
  token_identity_user_id: string | null;
  redirect_uris: string[];
  allowed_grant_types: OAuthAppDto['allowedGrantTypes'];
  allowed_scopes: string[];
  status: OAuthAppStatus;
  logo_url: string | null;
  homepage_url: string | null;
  privacy_policy_url: string | null;
  terms_of_service_url: string | null;
  is_confidential: boolean;
  is_dynamic: boolean;
  access_token_lifetime: number;
  refresh_token_lifetime: number;
  knowledge_limit: KnowledgeLimit | null;
  is_deleted: boolean;
  deleted_by: string | null;
  version: number;
  created_at: Date;
  updated_at: Date;
}

const COLS = `id, slug, client_id, client_secret_encrypted, name, description, org_id, created_by, token_identity_user_id, redirect_uris,
  allowed_grant_types, allowed_scopes, status, logo_url, homepage_url, privacy_policy_url, terms_of_service_url, is_confidential, is_dynamic,
  access_token_lifetime, refresh_token_lifetime, knowledge_limit, is_deleted, deleted_by, version, created_at, updated_at`;

/** The optional text fields of the stored row: a NULL column is a key left out, as an unset field was. */
const OPTIONAL: Array<[keyof AppRow, keyof OAuthAppDto]> = [
  ['description', 'description'],
  ['token_identity_user_id', 'tokenIdentityUserId'],
  ['logo_url', 'logoUrl'],
  ['homepage_url', 'homepageUrl'],
  ['privacy_policy_url', 'privacyPolicyUrl'],
  ['terms_of_service_url', 'termsOfServiceUrl'],
  ['deleted_by', 'deletedBy'],
];

export function appDto(r: AppRow): OAuthAppDto {
  const o: Record<string, unknown> = {
    _id: r.id,
    slug: r.slug,
    clientId: r.client_id,
    clientSecretEncrypted: r.client_secret_encrypted,
    name: r.name,
    orgId: r.org_id,
    createdBy: r.created_by,
    redirectUris: r.redirect_uris,
    allowedGrantTypes: r.allowed_grant_types,
    allowedScopes: r.allowed_scopes,
    status: r.status,
    isConfidential: r.is_confidential,
    isDynamic: r.is_dynamic,
    accessTokenLifetime: r.access_token_lifetime,
    refreshTokenLifetime: r.refresh_token_lifetime,
    isDeleted: r.is_deleted,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    __v: r.version,
  };
  for (const [col, key] of OPTIONAL) if (r[col] !== null) o[key] = r[col];
  if (r.knowledge_limit !== null) o.knowledgeLimit = r.knowledge_limit;
  return o as unknown as OAuthAppDto;
}

/** patch key -> [column, replaces an array]. An array replaced counts as a new revision, as a document save did. */
const PATCH: Record<string, [string, boolean]> = {
  name: ['name', false],
  description: ['description', false],
  redirectUris: ['redirect_uris', true],
  allowedGrantTypes: ['allowed_grant_types', true],
  allowedScopes: ['allowed_scopes', true],
  homepageUrl: ['homepage_url', false],
  privacyPolicyUrl: ['privacy_policy_url', false],
  termsOfServiceUrl: ['terms_of_service_url', false],
  accessTokenLifetime: ['access_token_lifetime', false],
  refreshTokenLifetime: ['refresh_token_lifetime', false],
  knowledgeLimit: ['knowledge_limit', false],
  clientSecretEncrypted: ['client_secret_encrypted', false],
  status: ['status', false],
  isDeleted: ['is_deleted', false],
  deletedBy: ['deleted_by', false],
  tokenIdentityUserId: ['token_identity_user_id', false],
};

/** The apps a person manages in Developer Settings: theirs, live, not an internal or dynamically registered client. */
function owned(p: Params, orgId: string, userId: string): string {
  return `org_id = ${p.add(orgId)} AND created_by = ${p.add(userId)} AND NOT is_deleted AND NOT is_dynamic AND client_id <> ${p.add(FIRST_PARTY_DEVICE_CLIENT_ID)}
    AND NOT starts_with(client_id, ${p.add(PAT_APP_CLIENT_ID_PREFIX)}) AND NOT starts_with(client_id, ${p.add(SERVICE_TOKEN_APP_CLIENT_ID_PREFIX)})`;
}

export class PgOAuthAppRepository implements OAuthAppRepository {
  async create(input: NewOAuthApp): Promise<OAuthAppDto> {
    const i = stripNul(input, 'oauth-app', 'app');
    return inOrgUnit(i.orgId, undefined, true, async (k) => {
      const { rows: n } = await k.query<{ n: number }>(
        `/* pipeshub:oauth.app_slug */ INSERT INTO identity.slug_counters (org_id, kind, next_value) VALUES ($1, 'OAuthApp', 1001)
           ON CONFLICT (org_id, kind) DO UPDATE SET next_value = identity.slug_counters.next_value + 1 RETURNING next_value AS n`,
        [k.orgId],
      );
      const now = new Date();
      const { rows } = await k.query<AppRow>(
        `/* pipeshub:oauth.app_create */ INSERT INTO oauth.apps (org_id, id, slug, client_id, client_secret_encrypted, name, description, created_by,
           redirect_uris, allowed_grant_types, allowed_scopes, status, logo_url, homepage_url, privacy_policy_url, terms_of_service_url,
           is_confidential, is_dynamic, access_token_lifetime, refresh_token_lifetime, knowledge_limit, created_at, updated_at, token_identity_user_id)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $22, $23) RETURNING ${COLS}`,
        [
          k.orgId,
          newObjectId(),
          slugify(`OAuthApp-${String(n[0]?.n)}`),
          i.clientId,
          i.clientSecretEncrypted,
          i.name,
          i.description ?? null,
          i.createdBy,
          i.redirectUris,
          i.allowedGrantTypes,
          i.allowedScopes,
          i.status ?? OAuthAppStatus.ACTIVE,
          i.logoUrl ?? null,
          i.homepageUrl ?? null,
          i.privacyPolicyUrl ?? null,
          i.termsOfServiceUrl ?? null,
          i.isConfidential,
          i.isDynamic ?? false,
          i.accessTokenLifetime ?? 3600,
          i.refreshTokenLifetime ?? 2592000,
          i.knowledgeLimit ?? null,
          now,
          i.tokenIdentityUserId ?? null,
        ],
      );
      return appDto(rows[0] as AppRow);
    });
  }

  async findOwned(
    orgId: string,
    userId: string,
    appId: string,
  ): Promise<OAuthAppDto | null> {
    const id = oidOf(appId);
    const user = oidOf(userId);
    if (!id || !user) return null;
    return inOrgUnit(orgId, undefined, false, async (k) => {
      const p = new Params();
      const where = owned(p, k.orgId, user);
      const { rows } = await k.query<AppRow>(
        `/* pipeshub:oauth.app_find_owned */ SELECT ${COLS} FROM oauth.apps WHERE ${where} AND id = ${p.add(id)}`,
        p.values,
      );
      return rows[0] ? appDto(rows[0]) : null;
    });
  }

  async listOwned(
    orgId: string,
    userId: string,
    q: OwnedAppListQuery,
  ): Promise<{ rows: OAuthAppDto[]; total: number }> {
    const user = oidOf(userId);
    if (!user) return { rows: [], total: 0 };
    return inOrgUnit(orgId, undefined, false, async (k) => {
      const p = new Params();
      let where = owned(p, k.orgId, user);
      if (q.status) where += ` AND status = ${p.add(q.status)}`;
      if (q.search) {
        const like = p.add(`%${likeEscape(q.search)}%`);
        where += ` AND (name ILIKE ${like} OR description ILIKE ${like})`;
      }
      const total = await k.query<{ n: string }>(
        `/* pipeshub:oauth.app_count_owned */ SELECT count(*) AS n FROM oauth.apps WHERE ${where}`,
        p.values,
      );
      const page = await k.query<AppRow>(
        `/* pipeshub:oauth.app_list_owned */ SELECT ${COLS} FROM oauth.apps WHERE ${where} ORDER BY created_at DESC, id DESC LIMIT ${p.add(q.limit)} OFFSET ${p.add(q.skip)}`,
        p.values,
      );
      return {
        rows: page.rows.map(appDto),
        total: Number(total.rows[0]?.n ?? 0),
      };
    });
  }

  async updateOwned(
    orgId: string,
    userId: string,
    appId: string,
    patch: OAuthAppPatch,
    opts?: UpdateOwnedOptions,
  ): Promise<OAuthAppDto | null> {
    const id = oidOf(appId);
    const user = oidOf(userId);
    if (!id || !user) return null;
    const clean = stripNul(patch, 'oauth-app', 'patch') as Record<
      string,
      unknown
    >;
    return inOrgUnit(orgId, undefined, true, async (k) => {
      const lock = new Params();
      const p = new Params();
      const where = `${owned(p, k.orgId, user)} AND id = ${p.add(id)}`;
      const sets: string[] = [];
      let arrays = false;
      for (const [key, value] of Object.entries(clean)) {
        const col = PATCH[key];
        if (!col) throw new Error(`oauth app patch: unknown field ${key}`);
        arrays ||= col[1];
        sets.push(`${col[0]} = ${p.add(value === undefined ? null : value)}`);
      }
      // the row is locked first, so two replacements of an array queue instead of overwriting each other's revision
      const locked = await k.query(
        `/* pipeshub:oauth.app_lock_owned */ SELECT 1 FROM oauth.apps WHERE ${owned(lock, k.orgId, user)} AND id = ${lock.add(id)} FOR UPDATE`,
        lock.values,
      );
      if (locked.rows.length === 0) return null;
      sets.push(`updated_at = ${p.add(new Date())}`);
      if (arrays) sets.push('version = version + 1');
      // saved only over the limit the caller read: the row is locked, so the comparison and the write cannot be split
      const stale =
        opts && 'overKnowledgeLimit' in opts
          ? ` AND knowledge_limit IS NOT DISTINCT FROM ${p.add(opts.overKnowledgeLimit === undefined || opts.overKnowledgeLimit === null ? null : JSON.stringify(opts.overKnowledgeLimit))}::jsonb`
          : '';
      const { rows } = await k.query<AppRow>(
        `/* pipeshub:oauth.app_update_owned */ UPDATE oauth.apps SET ${sets.join(', ')} WHERE ${where}${stale} RETURNING ${COLS}`,
        p.values,
      );
      if (rows[0]) return appDto(rows[0]);
      if (stale === '') return null;
      // a fresh parameter list: `lock` already holds the values of the lock query
      const reread = new Params();
      const now = await k.query<AppRow>(
        `/* pipeshub:oauth.app_get_owned */ SELECT ${COLS} FROM oauth.apps WHERE ${owned(reread, k.orgId, user)} AND id = ${reread.add(id)}`,
        reread.values,
      );
      if (opts?.ignoreStale && now.rows[0]) return appDto(now.rows[0]);
      throw new ConflictError(
        'This application changed while you were editing it. Reload it and save again.',
      );
    });
  }

  /**
   * The app of a client id: in the ambient org, else in the install's organisation.
   */
  private async byClient(
    clientId: string,
    cols: string,
    live: boolean,
    pick: (r: AppRow) => unknown,
  ): Promise<unknown> {
    const own = await inKeyUnit<unknown>(
      () => clientOrg(clientId),
      false,
      null,
      async (k) => {
        if (!goodKey(clientId)) return null;
        const { rows } = await k.query<AppRow>(
          `/* pipeshub:oauth.app_by_client */ SELECT ${cols} FROM oauth.apps WHERE org_id = $1 AND client_id = $2${live ? ' AND NOT is_deleted' : ''} LIMIT 1`,
          [k.orgId, clientId],
        );
        return rows[0] ? pick(rows[0]) : null;
      },
    );
    return own;
  }

  findLiveByClientId(clientId: string): Promise<OAuthAppDto | null> {
    return this.byClient(
      clientId,
      COLS,
      true,
      appDto,
    ) as Promise<OAuthAppDto | null>;
  }

  findLiveAllowedScopes(clientId: string): Promise<{
    allowedScopes: string[];
    knowledgeLimit?: KnowledgeLimit;
  } | null> {
    return this.byClient(
      clientId,
      'allowed_scopes, knowledge_limit',
      true,
      (r) => ({
        allowedScopes: r.allowed_scopes,
        ...(r.knowledge_limit !== null && {
          knowledgeLimit: r.knowledge_limit,
        }),
      }),
    ) as Promise<{
      allowedScopes: string[];
      knowledgeLimit?: KnowledgeLimit;
    } | null>;
  }

  findLiveIdentity(
    clientId: string,
  ): Promise<{ createdBy: string; tokenIdentityUserId?: string } | null> {
    return this.byClient(
      clientId,
      'created_by, token_identity_user_id',
      true,
      (r) => ({
        createdBy: r.created_by,
        ...(r.token_identity_user_id !== null && {
          tokenIdentityUserId: r.token_identity_user_id,
        }),
      }),
    ) as Promise<{ createdBy: string; tokenIdentityUserId?: string } | null>;
  }

  findByClientId(clientId: string): Promise<OAuthAppDto | null> {
    return this.byClient(
      clientId,
      COLS,
      false,
      appDto,
    ) as Promise<OAuthAppDto | null>;
  }

  async addAllowedScopes(id: string, scopes: string[]): Promise<void> {
    const oid = oidOf(id);
    if (!oid || scopes.length === 0) return;
    const wanted = stripNul(scopes, 'oauth-app', 'scopes');
    await inUnit(undefined, { write: true }, async (k) => {
      await k.query(
        `/* pipeshub:oauth.app_add_scopes */ UPDATE oauth.apps SET allowed_scopes = allowed_scopes || ARRAY(
            SELECT u.s FROM unnest($3::text[]) WITH ORDINALITY AS u(s, o) WHERE u.s <> ALL(allowed_scopes) GROUP BY u.s ORDER BY min(u.o)),
            updated_at = $4 WHERE org_id = $1 AND id = $2::text`,
        [k.orgId, oid, wanted, new Date()],
      );
    });
  }

  async listClientIdsActingAs(userId: string): Promise<string[]> {
    const id = oidOf(userId);
    if (!id) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      const { rows } = await k.query<{ client_id: string }>(
        `/* pipeshub:oauth.app_acting_as */ SELECT client_id FROM oauth.apps WHERE org_id = $1 AND token_identity_user_id = $2 AND NOT is_deleted ORDER BY created_at, id`,
        [k.orgId, id],
      );
      return rows.map((r) => r.client_id);
    });
  }

  async listClientIdsCreatedBy(
    orgId: string,
    userId: string,
  ): Promise<string[]> {
    const id = oidOf(userId);
    if (!id) return [];
    return inOrgUnit(orgId, undefined, false, async (k) => {
      const { rows } = await k.query<{ client_id: string }>(
        `/* pipeshub:oauth.app_created_by */ SELECT client_id FROM oauth.apps WHERE org_id = $1 AND created_by = $2 AND NOT is_deleted ORDER BY created_at, id`,
        [k.orgId, id],
      );
      return rows.map((r) => r.client_id);
    });
  }

  async softDeleteCreatedBy(
    orgId: string,
    userId: string,
    deletedBy: string,
  ): Promise<void> {
    const user = oidOf(userId);
    const by = oidOf(deletedBy);
    if (!user || !by) return;
    await inOrgUnit(orgId, undefined, true, (k) =>
      k.query(
        `/* pipeshub:oauth.app_soft_delete_created_by */ UPDATE oauth.apps SET is_deleted = true, status = 'revoked', deleted_by = $3, updated_at = $4
           WHERE org_id = $1 AND created_by = $2 AND NOT is_deleted`,
        [k.orgId, user, by, new Date()],
      ),
    );
  }
}
