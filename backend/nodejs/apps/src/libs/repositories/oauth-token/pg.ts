/**
 * PostgreSQL implementation of the OAuth token repository (`DOC_STORE=postgres`): access and refresh tokens, authorization codes
 * and device authorizations. Tokens, codes and devices are org-scoped rows; a call that holds only a hash or a client id runs as
 * the install's organisation (pg-org.ts). Codes are stored as their SHA-256.
 */
import type { KnowledgeLimit } from '../../../modules/oauth_provider/types/oauth.types';
import { currentOrgContext } from '../../context/orgContext';
import { ConflictError, InternalServerError } from '../../errors/http.errors';
import { UniqueConstraintError } from '../errors';
import { stripNul } from '../nul';
import { hashOpaqueToken } from './token-hash';
import {
  assertOrg,
  inUnit,
  newObjectId,
  oidOf,
  type KernelTx,
} from '../pg-util';
import {
  accessOrg,
  clientOrg,
  goodKey,
  installOrg,
  inKeyUnit,
  inOrgUnit,
  refreshOrg,
} from '../oauth-app/pg-org';
import {
  OAuthDeviceCodeStatus,
  REFRESH_ROTATED,
  REFRESH_REUSE_DETECTED,
  type AccessTokenDto,
  type AuthorizationCodeDto,
  type DeviceCodeDto,
  type NewAccessToken,
  type NewAuthorizationCode,
  type NewDeviceCode,
  type NewRefreshToken,
  type OAuthTokenRepository,
  type RefreshTokenDto,
} from './types';

/** A value in the position of an id: undefined when it cannot be one (such a value matches nothing). */
const id24 = (v: unknown): string | undefined =>
  typeof v === 'string' ? oidOf(v) : undefined;

interface AccessRow {
  id: string;
  org_id: string;
  token_hash: string;
  client_id: string;
  user_id: string | null;
  scopes: string[];
  expires_at: Date;
  is_revoked: boolean;
  revoked_at: Date | null;
  revoked_by: string | null;
  revoked_reason: string | null;
  parent_refresh_token_id: string | null;
  name: string | null;
  last_used_at: Date | null;
  actor_user_id: string | null;
  knowledge_limit: KnowledgeLimit | null;
  version: number;
  created_at: Date;
  updated_at: Date;
}
interface RefreshRow {
  id: string;
  org_id: string;
  token_hash: string;
  client_id: string;
  user_id: string;
  scopes: string[];
  expires_at: Date;
  is_revoked: boolean;
  revoked_at: Date | null;
  revoked_by: string | null;
  revoked_reason: string | null;
  rotation_count: number;
  previous_token_hash: string | null;
  knowledge_limit: KnowledgeLimit | null;
  /** The limit column is not NULL: a JSON null is "no limit", a SQL NULL marks a token stored before the limit was kept. */
  kl_kept: boolean;
  version: number;
  created_at: Date;
  updated_at: Date;
}
interface CodeRow {
  id: string;
  org_id: string;
  code_challenge: string | null;
  code_challenge_method: 'S256' | 'plain' | null;
  client_id: string;
  user_id: string;
  redirect_uri: string;
  scopes: string[];
  knowledge_limit: KnowledgeLimit | null;
  expires_at: Date;
  is_used: boolean;
  used_at: Date | null;
  replayed_at: Date | null;
  version: number;
  created_at: Date;
  updated_at: Date;
}
interface DeviceRow {
  dev_id: string;
  dev_hash: string;
  dev_user_code: string;
  dev_client: string;
  dev_scopes: string[];
  dev_status: OAuthDeviceCodeStatus;
  dev_user: string | null;
  dev_org: string | null;
  dev_interval: number;
  dev_polled: Date | null;
  dev_expires: Date;
  dev_created: Date;
  dev_updated: Date;
  dev_version: number;
  /** The knowledge limit as three lists (all NULL: no limit). */
  dev_limit_apps: string[] | null;
  dev_limit_groups: string[] | null;
  dev_limit_records: string[] | null;
}

const DEVICE = `d.id AS dev_id, d.device_code_hash AS dev_hash, d.user_code AS dev_user_code, d.client_id AS dev_client, d.scopes AS dev_scopes,
  d.status AS dev_status, d.approved_user_id AS dev_user, (CASE WHEN d.approved_user_id IS NOT NULL THEN d.org_id END) AS dev_org, d.poll_interval AS dev_interval, d.last_polled_at AS dev_polled,
  d.expires_at AS dev_expires, d.created_at AS dev_created, d.updated_at AS dev_updated, d.version AS dev_version,
  d.limit_apps AS dev_limit_apps, d.limit_groups AS dev_limit_groups, d.limit_records AS dev_limit_records`;
const ACCESS = `id, org_id, token_hash, client_id, user_id, scopes, expires_at, is_revoked, revoked_at, revoked_by, revoked_reason,
  parent_refresh_token_id, name, last_used_at, actor_user_id, knowledge_limit, version, created_at, updated_at`;
const REFRESH = `id, org_id, token_hash, client_id, user_id, scopes, expires_at, is_revoked, revoked_at, revoked_by, revoked_reason,
  rotation_count, previous_token_hash, knowledge_limit, (knowledge_limit IS NOT NULL) AS kl_kept, version, created_at, updated_at`;
const CODE = `id, org_id, code_challenge, code_challenge_method, client_id, user_id, redirect_uri, scopes, knowledge_limit, expires_at, is_used, used_at, replayed_at,
  version, created_at, updated_at`;

/** Copies the columns that are not NULL under their DTO names. */
function optional(
  o: Record<string, unknown>,
  r: object,
  map: Array<[string, string]>,
): void {
  for (const [col, key] of map) {
    const v = (r as Record<string, unknown>)[col];
    if (v !== null) o[key] = v;
  }
}

const accessDto = (r: AccessRow): AccessTokenDto => {
  const o: Record<string, unknown> = {
    _id: r.id,
    tokenHash: r.token_hash,
    clientId: r.client_id,
    orgId: r.org_id,
    scopes: r.scopes,
    expiresAt: r.expires_at,
    isRevoked: r.is_revoked,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    __v: r.version,
  };
  optional(o, r, [
    ['user_id', 'userId'],
    ['revoked_at', 'revokedAt'],
    ['revoked_by', 'revokedBy'],
    ['revoked_reason', 'revokedReason'],
    ['parent_refresh_token_id', 'parentRefreshTokenId'],
    ['name', 'name'],
    ['last_used_at', 'lastUsedAt'],
    ['actor_user_id', 'actorUserId'],
    ['knowledge_limit', 'knowledgeLimit'],
  ]);
  return o as unknown as AccessTokenDto;
};
const refreshDto = (r: RefreshRow): RefreshTokenDto => {
  const o: Record<string, unknown> = {
    _id: r.id,
    tokenHash: r.token_hash,
    clientId: r.client_id,
    userId: r.user_id,
    orgId: r.org_id,
    scopes: r.scopes,
    expiresAt: r.expires_at,
    isRevoked: r.is_revoked,
    rotationCount: r.rotation_count,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    __v: r.version,
  };
  optional(o, r, [
    ['revoked_at', 'revokedAt'],
    ['revoked_by', 'revokedBy'],
    ['revoked_reason', 'revokedReason'],
    ['previous_token_hash', 'previousTokenHash'],
  ]);
  if (r.kl_kept) o.knowledgeLimit = r.knowledge_limit;
  return o as unknown as RefreshTokenDto;
};
const codeDto = (r: CodeRow, code: string): AuthorizationCodeDto => {
  const o: Record<string, unknown> = {
    _id: r.id,
    code,
    clientId: r.client_id,
    userId: r.user_id,
    orgId: r.org_id,
    redirectUri: r.redirect_uri,
    scopes: r.scopes,
    expiresAt: r.expires_at,
    isUsed: r.is_used,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    __v: r.version,
  };
  optional(o, r, [
    ['code_challenge', 'codeChallenge'],
    ['code_challenge_method', 'codeChallengeMethod'],
    ['used_at', 'usedAt'],
    ['replayed_at', 'replayedAt'],
    ['knowledge_limit', 'knowledgeLimit'],
  ]);
  return o as unknown as AuthorizationCodeDto;
};
const deviceDto = (r: DeviceRow): DeviceCodeDto => {
  const o: Record<string, unknown> = {
    _id: r.dev_id,
    deviceCodeHash: r.dev_hash,
    userCode: r.dev_user_code,
    clientId: r.dev_client,
    scopes: r.dev_scopes,
    status: r.dev_status,
    interval: r.dev_interval,
    expiresAt: r.dev_expires,
    createdAt: r.dev_created,
    updatedAt: r.dev_updated,
    __v: r.dev_version,
  };
  optional(o, r, [
    ['dev_user', 'userId'],
    ['dev_org', 'orgId'],
    ['dev_polled', 'lastPolledAt'],
  ]);
  if (r.dev_limit_apps !== null)
    o.knowledgeLimit = {
      apps: r.dev_limit_apps,
      recordGroups: r.dev_limit_groups ?? [],
      records: r.dev_limit_records ?? [],
    } satisfies KnowledgeLimit;
  return o as unknown as DeviceCodeDto;
};

export class PgOAuthTokenRepository implements OAuthTokenRepository {
  // ---- issue ----------------------------------------------------------------------------------------------------------------
  async issueAccess(input: NewAccessToken): Promise<AccessTokenDto> {
    const i = stripNul(input, 'oauth-token', 'access');
    return inOrgUnit(i.orgId, undefined, true, async (k) => {
      const { rows } = await k.query<AccessRow>(
        `/* pipeshub:oauth.access_issue */ INSERT INTO oauth.access_tokens (org_id, id, token_hash, client_id, user_id, scopes, expires_at, name, actor_user_id, knowledge_limit, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11) RETURNING ${ACCESS}`,
        [
          k.orgId,
          newObjectId(),
          i.tokenHash,
          i.clientId,
          i.userId ?? null,
          i.scopes,
          i.expiresAt,
          i.name ?? null,
          i.actorUserId ?? null,
          i.knowledgeLimit ?? null,
          new Date(),
        ],
      );
      return accessDto(rows[0] as AccessRow);
    });
  }

  async issueRefresh(input: NewRefreshToken): Promise<RefreshTokenDto> {
    const i = stripNul(input, 'oauth-token', 'refresh');
    return inOrgUnit(i.orgId, undefined, true, async (k) => {
      const { rows } = await k.query<RefreshRow>(
        `/* pipeshub:oauth.refresh_issue */ INSERT INTO oauth.refresh_tokens (org_id, id, token_hash, client_id, user_id, scopes, expires_at, knowledge_limit, created_at, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $9) RETURNING ${REFRESH}`,
        [
          k.orgId,
          newObjectId(),
          i.tokenHash,
          i.clientId,
          i.userId,
          i.scopes,
          i.expiresAt,
          i.knowledgeLimit === undefined
            ? null
            : JSON.stringify(i.knowledgeLimit),
          new Date(),
        ],
      );
      return refreshDto(rows[0] as RefreshRow);
    });
  }

  findRefreshByHash(tokenHash: string): Promise<RefreshTokenDto | null> {
    return inKeyUnit<RefreshTokenDto | null>(
      () => refreshOrg(tokenHash),
      false,
      null,
      async (k) => {
        if (!goodKey(tokenHash)) return null;
        const { rows } = await k.query<RefreshRow>(
          `/* pipeshub:oauth.refresh_by_hash */ SELECT ${REFRESH} FROM oauth.refresh_tokens WHERE org_id = $1 AND token_hash = $2`,
          [k.orgId, tokenHash],
        );
        return rows[0] ? refreshDto(rows[0]) : null;
      },
    );
  }

  // The org of an id alone is not knowable: these run in the ambient org (the token endpoint binds the client's).
  async rotateRefresh(id: string): Promise<boolean> {
    const rid = id24(id);
    if (!rid) return false;
    return inUnit(undefined, { write: true }, async (k) => {
      const now = new Date();
      const r = await k.query(
        `/* pipeshub:oauth.refresh_rotate */ UPDATE oauth.refresh_tokens SET is_revoked = true, revoked_at = $3, updated_at = $3, revoked_reason = $4 WHERE org_id = $1 AND id = $2 AND NOT is_revoked`,
        [k.orgId, rid, now, REFRESH_ROTATED],
      );
      return r.rowCount === 1;
    });
  }

  async flagRefreshReplayed(id: string): Promise<void> {
    const rid = id24(id);
    if (!rid) return;
    await inUnit(undefined, { write: true }, async (k) => {
      const now = new Date();
      await k.query(
        `/* pipeshub:oauth.refresh_flag_replayed */ UPDATE oauth.refresh_tokens SET revoked_reason = $3, updated_at = $4 WHERE org_id = $1 AND id = $2`,
        [k.orgId, rid, REFRESH_REUSE_DETECTED, now],
      );
    });
  }

  async touchAccessLastUsed(id: string, at: Date): Promise<void> {
    const tid = id24(id);
    if (!tid) return;
    await inUnit(undefined, { write: true }, (k) =>
      k.query(
        `/* pipeshub:oauth.access_touch */ UPDATE oauth.access_tokens SET last_used_at = $3, updated_at = $4 WHERE org_id = $1 AND id = $2`,
        [k.orgId, tid, at, new Date()],
      ),
    );
  }

  // ---- revoke ---------------------------------------------------------------------------------------------------------------
  revokeAccessByHash(tokenHash: string, clientId: string): Promise<boolean> {
    return inKeyUnit(
      () => accessOrg(tokenHash),
      true,
      false,
      async (k) => {
        if (!goodKey(tokenHash) || !goodKey(clientId)) return false;
        const now = new Date();
        const r = await k.query(
          `/* pipeshub:oauth.access_revoke_by_hash */ UPDATE oauth.access_tokens SET is_revoked = true, revoked_at = $4, updated_at = $4
           WHERE org_id = $1 AND token_hash = $2 AND client_id = $3 AND NOT is_revoked`,
          [k.orgId, tokenHash, clientId, now],
        );
        return (r.rowCount ?? 0) > 0;
      },
    );
  }

  revokeRefreshByHash(tokenHash: string, clientId: string): Promise<boolean> {
    return inKeyUnit(
      () => refreshOrg(tokenHash),
      true,
      false,
      async (k) => {
        if (!goodKey(tokenHash) || !goodKey(clientId)) return false;
        const now = new Date();
        const r = await k.query(
          `/* pipeshub:oauth.refresh_revoke_by_hash */ UPDATE oauth.refresh_tokens SET is_revoked = true, revoked_at = $4, updated_at = $4
           WHERE org_id = $1 AND token_hash = $2 AND client_id = $3 AND NOT is_revoked`,
          [k.orgId, tokenHash, clientId, now],
        );
        return (r.rowCount ?? 0) > 0;
      },
    );
  }

  async revokeIssued(accessHash: string, refreshHash?: string): Promise<void> {
    await inKeyUnit(
      () => accessOrg(accessHash),
      true,
      undefined,
      async (k) => {
        const now = new Date();
        await k.query(
          `/* pipeshub:oauth.access_revoke_issued */ UPDATE oauth.access_tokens SET is_revoked = true, revoked_at = $3, updated_at = $3 WHERE org_id = $1 AND token_hash = $2`,
          [k.orgId, accessHash, now],
        );
        if (refreshHash !== undefined)
          await k.query(
            `/* pipeshub:oauth.refresh_revoke_issued */ UPDATE oauth.refresh_tokens SET is_revoked = true, revoked_at = $3, updated_at = $3 WHERE org_id = $1 AND token_hash = $2`,
            [k.orgId, refreshHash, now],
          );
      },
    );
  }

  /** Both token tables: revoke the live tokens that match `where` (params after $1 org and $2 time). */
  private async revokeBoth(
    k: KernelTx,
    where: string,
    values: unknown[],
    reason?: string,
  ): Promise<void> {
    const set = `is_revoked = true, revoked_at = $2, updated_at = $2${reason === undefined ? '' : `, revoked_reason = $${String(values.length + 3)}`}`;
    const args = [
      k.orgId,
      new Date(),
      ...values,
      ...(reason === undefined ? [] : [reason]),
    ];
    for (const t of ['access_tokens', 'refresh_tokens'])
      await k.query(
        `/* pipeshub:oauth.${t}_revoke_where */ UPDATE oauth.${t} SET ${set} WHERE org_id = $1 AND NOT is_revoked AND ${where}`,
        args,
      );
  }

  async revokeRefreshWithoutKnowledgeLimit(clientId: string): Promise<void> {
    await inKeyUnit(
      () => clientOrg(clientId),
      true,
      undefined,
      async (k) => {
        if (!goodKey(clientId)) return;
        await k.query(
          `/* pipeshub:oauth.refresh_revoke_unlimited */ UPDATE oauth.refresh_tokens SET is_revoked = true, revoked_at = $3, updated_at = $3
            WHERE org_id = $1 AND client_id = $2 AND NOT is_revoked AND knowledge_limit IS NULL`,
          [k.orgId, clientId, new Date()],
        );
      },
    );
  }

  revokeAllForClient(clientId: string): Promise<void> {
    return inKeyUnit(
      () => clientOrg(clientId),
      true,
      undefined,
      (k) => this.revokeBoth(k, 'client_id = $3', [clientId]),
    );
  }

  async revokeAllForUser(userId: string): Promise<void> {
    const u = id24(userId);
    if (!u) return;
    await inUnit(undefined, { write: true }, (k) =>
      this.revokeBoth(k, 'user_id = $3', [u]),
    );
  }

  async revokeAllForClientUser(
    clientId: string,
    userId: string,
  ): Promise<void> {
    const u = id24(userId);
    if (!u) return;
    await inKeyUnit(
      () => clientOrg(clientId),
      true,
      undefined,
      (k) =>
        this.revokeBoth(k, 'client_id = $3 AND user_id = $4', [clientId, u]),
    );
  }

  async revokeAllForClientUserBecause(
    clientId: string,
    userId: string,
    reason: string,
  ): Promise<void> {
    const u = id24(userId);
    if (!u) return;
    await inKeyUnit(
      () => clientOrg(clientId),
      true,
      undefined,
      (k) =>
        this.revokeBoth(
          k,
          'client_id = $3 AND user_id = $4',
          [clientId, u],
          stripNul(reason, 'oauth-token', 'reason'),
        ),
    );
  }

  async revokeDelegatedForActor(
    actorUserId: string,
    revokedBy: string | undefined,
    reason: string,
  ): Promise<number> {
    const actor = id24(actorUserId);
    const by = revokedBy === undefined ? null : (id24(revokedBy) ?? null);
    if (!actor) return 0;
    return inUnit(undefined, { write: true }, async (k) => {
      const now = new Date();
      const r = await k.query(
        `/* pipeshub:oauth.access_revoke_delegated */ UPDATE oauth.access_tokens SET is_revoked = true, revoked_at = $3, updated_at = $3, revoked_reason = $4,
           revoked_by = COALESCE($5, revoked_by) WHERE org_id = $1 AND actor_user_id = $2 AND NOT is_revoked`,
        [k.orgId, actor, now, stripNul(reason, 'oauth-token', 'reason'), by],
      );
      return r.rowCount ?? 0;
    });
  }

  private revokeById(
    id: string,
    clientId: string,
    userId: string | null,
    revokedBy: string,
    reason?: string,
  ): Promise<boolean> {
    const tid = id24(id);
    const by = id24(revokedBy);
    if (!tid || !by || !goodKey(clientId)) return Promise.resolve(false);
    return inKeyUnit(
      () => clientOrg(clientId),
      true,
      false,
      async (k) => {
        const now = new Date();
        const r = await k.query(
          `/* pipeshub:oauth.access_revoke_by_id */ UPDATE oauth.access_tokens SET is_revoked = true, revoked_at = $5, updated_at = $5, revoked_by = $6, revoked_reason = $7
           WHERE org_id = $1 AND id = $2 AND client_id = $3 AND ($4::text IS NULL OR user_id = $4) AND NOT is_revoked`,
          [
            k.orgId,
            tid,
            clientId,
            userId,
            now,
            by,
            reason === undefined
              ? null
              : stripNul(reason, 'oauth-token', 'reason'),
          ],
        );
        return (r.rowCount ?? 0) > 0;
      },
    );
  }

  async revokeAccessById(
    id: string,
    clientId: string,
    userId: string,
    revokedBy: string,
    reason?: string,
  ): Promise<boolean> {
    const u = id24(userId);
    return u ? this.revokeById(id, clientId, u, revokedBy, reason) : false;
  }

  revokeAccessByIdForClient(
    id: string,
    clientId: string,
    revokedBy: string,
    reason?: string,
  ): Promise<boolean> {
    return this.revokeById(id, clientId, null, revokedBy, reason);
  }

  // ---- list -----------------------------------------------------------------------------------------------------------------
  async listLiveForClient(
    clientId: string,
    limit: number,
  ): Promise<{ access: AccessTokenDto[]; refresh: RefreshTokenDto[] }> {
    return inKeyUnit(
      () => clientOrg(clientId),
      false,
      { access: [], refresh: [] },
      async (k) => {
        const now = new Date();
        const a = await k.query<AccessRow>(
          `/* pipeshub:oauth.access_live_for_client */ SELECT ${ACCESS} FROM oauth.access_tokens WHERE org_id = $1 AND client_id = $2 AND NOT is_revoked AND expires_at > $3
           ORDER BY created_at DESC, id DESC LIMIT $4`,
          [k.orgId, clientId, now, limit],
        );
        const r = await k.query<RefreshRow>(
          `/* pipeshub:oauth.refresh_live_for_client */ SELECT ${REFRESH} FROM oauth.refresh_tokens WHERE org_id = $1 AND client_id = $2 AND NOT is_revoked AND expires_at > $3
           ORDER BY created_at DESC, id DESC LIMIT $4`,
          [k.orgId, clientId, now, limit],
        );
        return {
          access: a.rows.map(accessDto),
          refresh: r.rows.map(refreshDto),
        };
      },
    );
  }

  async listLiveAccessForUser(
    clientId: string,
    userId: string,
    limit: number,
  ): Promise<AccessTokenDto[]> {
    const u = id24(userId);
    if (!u) return [];
    return inKeyUnit(
      () => clientOrg(clientId),
      false,
      [] as AccessTokenDto[],
      async (k) => {
        const { rows } = await k.query<AccessRow>(
          `/* pipeshub:oauth.access_live_for_user */ SELECT ${ACCESS} FROM oauth.access_tokens WHERE org_id = $1 AND client_id = $2 AND user_id = $3 AND NOT is_revoked AND expires_at > $4
           ORDER BY created_at DESC, id DESC LIMIT $5`,
          [k.orgId, clientId, u, new Date(), limit],
        );
        return rows.map(accessDto);
      },
    );
  }

  async countLiveAccessForUser(
    clientId: string,
    userId: string,
  ): Promise<number> {
    const u = id24(userId);
    if (!u) return 0;
    return inKeyUnit(
      () => clientOrg(clientId),
      false,
      0,
      async (k) => {
        const { rows } = await k.query<{ n: string }>(
          `/* pipeshub:oauth.access_count_for_user */ SELECT count(*) AS n FROM oauth.access_tokens WHERE org_id = $1 AND client_id = $2 AND user_id = $3 AND NOT is_revoked AND expires_at > $4`,
          [k.orgId, clientId, u, new Date()],
        );
        return Number(rows[0]?.n ?? 0);
      },
    );
  }

  async listLiveAccessForClientPage(
    clientId: string,
    page: number,
    limit: number,
  ): Promise<{ rows: AccessTokenDto[]; total: number }> {
    return inKeyUnit(
      () => clientOrg(clientId),
      false,
      { rows: [], total: 0 },
      async (k) => {
        const now = new Date();
        const total = await k.query<{ n: string }>(
          `/* pipeshub:oauth.access_count_for_client */ SELECT count(*) AS n FROM oauth.access_tokens WHERE org_id = $1 AND client_id = $2 AND NOT is_revoked AND expires_at > $3`,
          [k.orgId, clientId, now],
        );
        const { rows } = await k.query<AccessRow>(
          `/* pipeshub:oauth.access_page_for_client */ SELECT ${ACCESS} FROM oauth.access_tokens WHERE org_id = $1 AND client_id = $2 AND NOT is_revoked AND expires_at > $3
           ORDER BY created_at DESC, id DESC LIMIT $4 OFFSET $5`,
          [k.orgId, clientId, now, limit, (page - 1) * limit],
        );
        return {
          rows: rows.map(accessDto),
          total: Number(total.rows[0]?.n ?? 0),
        };
      },
    );
  }

  // ---- authorization codes --------------------------------------------------------------------------------------------------
  async createCode(input: NewAuthorizationCode): Promise<void> {
    const i = stripNul(input, 'oauth-token', 'code');
    await inOrgUnit(i.orgId, undefined, true, (k) =>
      k.query(
        `/* pipeshub:oauth.code_create */ INSERT INTO oauth.authorization_codes (org_id, id, code_hash, code_challenge, code_challenge_method, client_id, user_id, redirect_uri, scopes,
           knowledge_limit, expires_at, created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $12)`,
        [
          k.orgId,
          newObjectId(),
          // the digest of the code as it was issued, the same value `findCode` digests (a generated code holds no U+0000)
          hashOpaqueToken(input.code),
          i.codeChallenge ?? null,
          i.codeChallengeMethod ?? null,
          i.clientId,
          i.userId,
          i.redirectUri,
          i.scopes,
          i.knowledgeLimit ?? null,
          i.expiresAt,
          new Date(),
        ],
      ),
    );
  }

  async findCode(
    code: string,
    clientId: string,
  ): Promise<AuthorizationCodeDto | null> {
    if (!goodKey(code)) return null;
    return inKeyUnit<AuthorizationCodeDto | null>(
      () => clientOrg(clientId),
      false,
      null,
      async (k) => {
        const { rows } = await k.query<CodeRow>(
          `/* pipeshub:oauth.code_find */ SELECT ${CODE} FROM oauth.authorization_codes WHERE org_id = $1 AND code_hash = $2 AND client_id = $3`,
          [k.orgId, hashOpaqueToken(code), clientId],
        );
        return rows[0] ? codeDto(rows[0], code) : null;
      },
    );
  }

  async consumeCode(code: string, clientId: string): Promise<boolean> {
    if (!goodKey(code)) return false;
    return inKeyUnit(
      () => clientOrg(clientId),
      true,
      false,
      async (k) => {
        const now = new Date();
        const r = await k.query(
          `/* pipeshub:oauth.code_consume */ UPDATE oauth.authorization_codes SET is_used = true, used_at = $4, updated_at = $4
           WHERE org_id = $1 AND code_hash = $2 AND client_id = $3 AND NOT is_used AND expires_at > $4`,
          [k.orgId, hashOpaqueToken(code), clientId, now],
        );
        return r.rowCount === 1;
      },
    );
  }

  async flagCodeReplayed(code: string, clientId: string): Promise<void> {
    if (!goodKey(code)) return;
    await inKeyUnit(
      () => clientOrg(clientId),
      true,
      undefined,
      async (k) => {
        await k.query(
          `/* pipeshub:oauth.code_flag_replayed */ UPDATE oauth.authorization_codes SET replayed_at = $4, updated_at = $4 WHERE org_id = $1 AND code_hash = $2 AND client_id = $3`,
          [k.orgId, hashOpaqueToken(code), clientId, new Date()],
        );
      },
    );
  }

  async deleteCode(id: string): Promise<void> {
    const cid = id24(id);
    if (!cid) return;
    await inUnit(undefined, { write: true }, (k) =>
      k.query(
        `/* pipeshub:oauth.code_delete */ DELETE FROM oauth.authorization_codes WHERE org_id = $1 AND id = $2`,
        [k.orgId, cid],
      ),
    );
  }

  // shortcut: the ambient org's codes only; the sweeper job removes every org's expired codes
  async deleteExpiredCodes(): Promise<number> {
    return inUnit(undefined, { write: true }, async (k) => {
      const r = await k.query(
        `/* pipeshub:oauth.code_delete_expired */ DELETE FROM oauth.authorization_codes WHERE org_id = $1 AND (expires_at < $2 OR is_used)`,
        [k.orgId, new Date()],
      );
      return r.rowCount ?? 0;
    });
  }

  async deleteUnusedCodesForUser(userId: string): Promise<void> {
    const u = id24(userId);
    if (!u) return;
    await inUnit(undefined, { write: true }, (k) =>
      k.query(
        `/* pipeshub:oauth.code_delete_for_user */ DELETE FROM oauth.authorization_codes WHERE org_id = $1 AND user_id = $2 AND NOT is_used`,
        [k.orgId, u],
      ),
    );
  }

  async deleteUnusedCodesForClient(clientId: string): Promise<void> {
    await inKeyUnit(
      () => clientOrg(clientId),
      true,
      undefined,
      async (k) => {
        await k.query(
          `/* pipeshub:oauth.code_delete_for_client */ DELETE FROM oauth.authorization_codes WHERE org_id = $1 AND client_id = $2 AND NOT is_used`,
          [k.orgId, clientId],
        );
      },
    );
  }

  // ---- device authorizations (the install's organisation) ------------------------------------------------------------------
  async createDevice(input: NewDeviceCode): Promise<void> {
    const i = stripNul(input, 'oauth-token', 'device');
    const ok = await inKeyUnit(installOrg, true, false, async (k) => {
      const id = newObjectId();
      const made = await k.query(
        `/* pipeshub:oauth.device_start */ INSERT INTO oauth.device_authorizations
           (org_id, id, device_code_hash, user_code, client_id, scopes, poll_interval, expires_at, limit_apps, limit_groups, limit_records, created_at, updated_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $12)
         ON CONFLICT (org_id, user_code) DO NOTHING`,
        [
          k.orgId,
          id,
          i.deviceCodeHash,
          i.userCode,
          i.clientId,
          i.scopes,
          i.interval,
          i.expiresAt,
          i.knowledgeLimit?.apps ?? null,
          i.knowledgeLimit?.recordGroups ?? null,
          i.knowledgeLimit?.records ?? null,
          new Date(),
        ],
      );
      return (made.rowCount ?? 0) > 0;
    });
    if (!ok)
      throw new UniqueConstraintError(
        'the device user code is already in use',
        { key: 'user_code' },
      );
  }

  async findDeviceByUserCode(userCode: string): Promise<DeviceCodeDto | null> {
    if (
      typeof userCode !== 'string' ||
      userCode.length < 1 ||
      userCode.length > 64 ||
      userCode.includes('\u0000')
    )
      return null;
    return inKeyUnit<DeviceCodeDto | null>(
      installOrg,
      false,
      null,
      async (k) => {
        const { rows } = await k.query<DeviceRow>(
          `/* pipeshub:oauth.device_by_user_code */ SELECT ${DEVICE} FROM oauth.device_authorizations d
          WHERE d.org_id = $1 AND d.user_code = $2 AND d.status <> 'claimed' LIMIT 1`,
          [k.orgId, userCode],
        );
        return rows[0] ? deviceDto(rows[0]) : null;
      },
    );
  }

  async findDeviceByHash(
    deviceCodeHash: string,
    clientId: string,
  ): Promise<DeviceCodeDto | null> {
    if (!goodKey(deviceCodeHash) || !goodKey(clientId)) return null;
    return inKeyUnit<DeviceCodeDto | null>(
      installOrg,
      false,
      null,
      async (k) => {
        const { rows } = await k.query<DeviceRow>(
          `/* pipeshub:oauth.device_by_hash */ SELECT ${DEVICE} FROM oauth.device_authorizations d
          WHERE d.org_id = $1 AND d.device_code_hash = $2 AND d.client_id = $3 AND d.status <> 'claimed' LIMIT 1`,
          [k.orgId, deviceCodeHash, clientId],
        );
        return rows[0] ? deviceDto(rows[0]) : null;
      },
    );
  }

  async decideDevice(
    id: string,
    decision:
      | { status: OAuthDeviceCodeStatus.DENIED }
      | {
          status: OAuthDeviceCodeStatus.APPROVED;
          userId: string;
          orgId: string;
        },
  ): Promise<void> {
    const did = id24(id);
    if (!did) return;
    if (decision.status === OAuthDeviceCodeStatus.DENIED) {
      await inKeyUnit(installOrg, true, undefined, async (k) => {
        await k.query(
          `/* pipeshub:oauth.device_deny */ UPDATE oauth.device_authorizations SET status = 'denied', updated_at = $3
            WHERE org_id = $1 AND id = $2 AND status IN ('pending', 'denied')`,
          [k.orgId, did, new Date()],
        );
      });
      return;
    }
    // the approving person and org are the unit's, so the arguments must say what the unit already is
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, decision.orgId);
      if (currentOrgContext().userId !== decision.userId)
        throw new InternalServerError(
          'the user of the call differs from the one the unit of work runs as',
        );
      const approved = await k.query(
        `/* pipeshub:oauth.device_approve */ UPDATE oauth.device_authorizations SET status = 'approved', approved_user_id = $3, updated_at = $4
          WHERE org_id = $1 AND id = $2 AND (status = 'pending' OR (status = 'approved' AND approved_user_id = $3))`,
        [k.orgId, did, decision.userId, new Date()],
      );
      // nothing matched: the request was decided by someone else first, or is not in this org. Say so rather than report an approval that never happened.
      if (approved.rowCount !== 1)
        throw new ConflictError(
          'This device authorization can no longer be approved.',
        );
    });
  }

  async recordDevicePoll(id: string, at: Date): Promise<void> {
    const did = id24(id);
    if (!did) return;
    await inKeyUnit(installOrg, true, undefined, async (k) => {
      await k.query(
        `/* pipeshub:oauth.device_poll */ UPDATE oauth.device_authorizations SET last_polled_at = $3
          WHERE org_id = $1 AND id = $2 AND status <> 'claimed'`,
        [k.orgId, did, at],
      );
    });
  }

  /** Takes an approved, unexpired authorization once: of any number of concurrent polls exactly one gets the row. */
  async claimApprovedDevice(id: string): Promise<DeviceCodeDto | null> {
    const did = id24(id);
    if (!did) return null;
    return inKeyUnit<DeviceCodeDto | null>(
      installOrg,
      true,
      null,
      async (k) => {
        const claimed = await k.query<DeviceRow>(
          `/* pipeshub:oauth.device_claim */ UPDATE oauth.device_authorizations d SET status = 'claimed', updated_at = $3
          WHERE d.org_id = $1 AND d.id = $2 AND d.status = 'approved' AND d.expires_at > now() RETURNING ${DEVICE}`,
          [k.orgId, did, new Date()],
        );
        const r = claimed.rows[0];
        return r
          ? deviceDto({ ...r, dev_status: OAuthDeviceCodeStatus.APPROVED })
          : null;
      },
    );
  }
}
