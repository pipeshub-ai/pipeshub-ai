/** Documents of the `oauthApps` collection as rows of `oauth.apps`. The per-organisation app slug counter is copied with the other counters. */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  limitJson,
  writePlan,
} from '../import';

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const list = (v: unknown, dflt: string[]): unknown[] =>
  Array.isArray(v) ? v : dflt;

export async function importOAuthAppDocs(
  k: KernelTx,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  const stats = emptyStats(docs.length);
  const rows: Row[] = [];
  for (const d of docs) {
    const id = idText(d._id);
    const org = idText(d.orgId);
    const by = idText(d.createdBy);
    if (
      !id ||
      !org ||
      !by ||
      typeof d.clientId !== 'string' ||
      typeof d.clientSecretEncrypted !== 'string' ||
      typeof d.name !== 'string' ||
      typeof d.createdAt !== 'string'
    ) {
      stats.skipped.push({
        id: String(d._id),
        reason:
          'app without a usable _id, orgId, createdBy, clientId, secret, name or createdAt',
      });
      continue;
    }
    rows.push({
      org_id: org,
      id,
      slug: typeof d.slug === 'string' ? d.slug : `oauthapp-${id}`,
      client_id: d.clientId,
      client_secret_encrypted: d.clientSecretEncrypted,
      name: d.name,
      description: str(d.description),
      created_by: by,
      token_identity_user_id: idText(d.tokenIdentityUserId),
      redirect_uris: list(d.redirectUris, []),
      allowed_grant_types: list(d.allowedGrantTypes, [
        'authorization_code',
        'refresh_token',
      ]),
      allowed_scopes: list(d.allowedScopes, []),
      status: d.status ?? 'active',
      logo_url: str(d.logoUrl),
      homepage_url: str(d.homepageUrl),
      privacy_policy_url: str(d.privacyPolicyUrl),
      terms_of_service_url: str(d.termsOfServiceUrl),
      is_confidential: d.isConfidential ?? true,
      is_dynamic: d.isDynamic ?? false,
      access_token_lifetime:
        typeof d.accessTokenLifetime === 'number'
          ? d.accessTokenLifetime
          : 3600,
      refresh_token_lifetime:
        typeof d.refreshTokenLifetime === 'number'
          ? d.refreshTokenLifetime
          : 2592000,
      knowledge_limit: limitJson(d.knowledgeLimit),
      is_deleted: d.isDeleted ?? false,
      deleted_by: idText(d.deletedBy),
      version: typeof d.__v === 'number' ? d.__v : 0,
      created_at: d.createdAt,
      updated_at: d.updatedAt ?? d.createdAt,
    });
  }
  await writePlan(k, [{ table: 'oauth.apps', rows }], stats);
  return stats;
}
