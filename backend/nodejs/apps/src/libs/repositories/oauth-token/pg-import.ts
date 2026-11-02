/**
 * Documents of `oauthAccessTokens`, `oauthRefreshTokens` and `authorizationCodes` as rows of `oauth.access_tokens`,
 * `oauth.refresh_tokens` and `oauth.authorization_codes`. Token hashes are copied as stored; an authorization code is stored
 * hashed (SHA-256), and the lookup hashes the code it is given, so a code in flight stays valid.
 */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  sha256Hex,
  writePlan,
  limitJson,
} from '../import';

/** A device authorization keeps the limit as three list columns (all NULL: no limit). */
const limitLists = (v: unknown): Row => {
  const l = limitJson(v);
  return {
    limit_apps: l?.apps ?? null,
    limit_groups: l?.recordGroups ?? null,
    limit_records: l?.records ?? null,
  };
};

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const strs = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const version = (d: NeutralDoc): number =>
  typeof d.__v === 'number' ? d.__v : 0;

interface Spec {
  table: string;
  what: string;
  row(d: NeutralDoc, id: string, org: string, created: string): Row | null;
}

const revocation = (d: NeutralDoc): Row => ({
  is_revoked: d.isRevoked ?? false,
  revoked_at: d.revokedAt ?? null,
  revoked_by: idText(d.revokedBy),
  revoked_reason: str(d.revokedReason),
});

const SPECS: Record<string, Spec> = {
  oauthAccessTokens: {
    table: 'oauth.access_tokens',
    what: 'access token',
    row: (d, id, org, created) =>
      typeof d.tokenHash === 'string' &&
      typeof d.clientId === 'string' &&
      typeof d.expiresAt === 'string'
        ? {
            org_id: org,
            id,
            token_hash: d.tokenHash,
            client_id: d.clientId,
            user_id: idText(d.userId),
            scopes: strs(d.scopes),
            expires_at: d.expiresAt,
            ...revocation(d),
            parent_refresh_token_id: idText(d.parentRefreshTokenId),
            name: str(d.name),
            last_used_at: d.lastUsedAt ?? null,
            actor_user_id: idText(d.actorUserId),
            knowledge_limit: limitJson(d.knowledgeLimit),
            version: version(d),
            created_at: created,
            updated_at: d.updatedAt ?? created,
          }
        : null,
  },
  oauthRefreshTokens: {
    table: 'oauth.refresh_tokens',
    what: 'refresh token',
    row: (d, id, org, created) =>
      typeof d.tokenHash === 'string' &&
      typeof d.clientId === 'string' &&
      typeof d.expiresAt === 'string' &&
      idText(d.userId)
        ? {
            org_id: org,
            id,
            token_hash: d.tokenHash,
            client_id: d.clientId,
            user_id: idText(d.userId),
            scopes: strs(d.scopes),
            expires_at: d.expiresAt,
            ...revocation(d),
            rotation_count:
              typeof d.rotationCount === 'number' ? d.rotationCount : 0,
            previous_token_hash: str(d.previousTokenHash),
            knowledge_limit: limitJson(d.knowledgeLimit),
            version: version(d),
            created_at: created,
            updated_at: d.updatedAt ?? created,
          }
        : null,
  },
  authorizationCodes: {
    table: 'oauth.authorization_codes',
    what: 'authorization code',
    row: (d, id, org, created) =>
      typeof d.code === 'string' &&
      typeof d.clientId === 'string' &&
      typeof d.redirectUri === 'string' &&
      typeof d.expiresAt === 'string' &&
      idText(d.userId)
        ? {
            org_id: org,
            id,
            code_hash: sha256Hex(d.code),
            code_challenge: str(d.codeChallenge),
            code_challenge_method: str(d.codeChallengeMethod),
            client_id: d.clientId,
            user_id: idText(d.userId),
            redirect_uri: d.redirectUri,
            scopes: strs(d.scopes),
            knowledge_limit: limitJson(d.knowledgeLimit),
            expires_at: d.expiresAt,
            is_used: d.isUsed ?? false,
            used_at: d.usedAt ?? null,
            replayed_at: d.replayedAt ?? null,
            version: version(d),
            created_at: created,
            updated_at: d.updatedAt ?? created,
          }
        : null,
  },
};

/**
 * Pending device authorizations. The application keeps them all in the install's organisation (the oldest live one, as the sign-in
 * flow looks them up before it knows who is signing in), so they are copied there whatever organisation approved them; one that
 * has no live organisation to hang on is skipped, and so is one that has expired.
 */
async function importDeviceDocs(
  k: KernelTx,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  const stats = emptyStats(docs.length);
  const org = (
    await k.query<{ org_id: string }>(
      '/* pipeshub:import.install_org */ SELECT o.org_id FROM identity.orgs o WHERE NOT o.is_deleted ORDER BY o.created_at, o.org_id LIMIT 1',
    )
  ).rows[0]?.org_id;
  const rows: Row[] = [];
  const now = Date.now();
  for (const d of docs) {
    const id = idText(d._id);
    if (typeof d.expiresAt === 'string' && Date.parse(d.expiresAt) <= now) {
      stats.skipped.push({
        id: String(d._id),
        reason: 'device authorization has expired',
      });
      continue;
    }
    if (
      org &&
      id &&
      typeof d.deviceCodeHash === 'string' &&
      typeof d.userCode === 'string' &&
      typeof d.clientId === 'string' &&
      typeof d.expiresAt === 'string'
    )
      rows.push({
        org_id: org,
        id,
        device_code_hash: d.deviceCodeHash,
        user_code: d.userCode,
        client_id: d.clientId,
        scopes: strs(d.scopes),
        status: str(d.status) ?? 'pending',
        approved_user_id: idText(d.userId),
        poll_interval: typeof d.interval === 'number' ? d.interval : 5,
        last_polled_at: d.lastPolledAt ?? null,
        expires_at: d.expiresAt,
        ...limitLists(d.knowledgeLimit),
        version: version(d),
        created_at: d.createdAt ?? d.expiresAt,
        updated_at: d.updatedAt ?? d.createdAt ?? d.expiresAt,
      });
    else
      stats.skipped.push({
        id: String(d._id),
        reason: org
          ? 'device authorization without a usable _id, hash, code, client or expiry'
          : 'device authorization with no organisation to hold it',
      });
  }
  await writePlan(k, [{ table: 'oauth.device_authorizations', rows }], stats);
  return stats;
}

export async function importOAuthTokenDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  if (collection === 'oauthDeviceCodes') return importDeviceDocs(k, docs);
  const spec = SPECS[collection];
  if (!spec)
    throw new Error(
      `the OAuth token repository does not import '${collection}'`,
    );
  const stats = emptyStats(docs.length);
  const rows: Row[] = [];
  for (const d of docs) {
    const id = idText(d._id);
    const org = idText(d.orgId);
    const created = typeof d.createdAt === 'string' ? d.createdAt : null;
    const row = id && org && created ? spec.row(d, id, org, created) : null;
    if (row) rows.push(row);
    else
      stats.skipped.push({
        id: String(d._id),
        reason: `${spec.what} without a usable _id, orgId, hash/code, client, expiry or createdAt`,
      });
  }
  await writePlan(k, [{ table: spec.table, rows }], stats);
  return stats;
}
