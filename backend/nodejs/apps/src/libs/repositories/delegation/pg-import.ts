/** Documents of `serviceAccountDelegations` and `delegationEvents` as rows of `oauth.service_account_delegations` and `oauth.delegation_events`. */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const version = (d: NeutralDoc): number =>
  typeof d.__v === 'number' ? d.__v : 0;

export async function importDelegationDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  const stats = emptyStats(docs.length);
  const rows: Row[] = [];
  const grants = collection === 'serviceAccountDelegations';
  if (!grants && collection !== 'delegationEvents')
    throw new Error(
      `the delegation repository does not import '${collection}'`,
    );
  for (const d of docs) {
    const id = idText(d._id);
    const org = idText(d.orgId);
    const account = idText(d.serviceAccountId);
    const created = typeof d.createdAt === 'string' ? d.createdAt : null;
    if (!id || !org || !account || !created) {
      stats.skipped.push({
        id: String(d._id),
        reason:
          'delegation record without a usable _id, orgId, serviceAccountId or createdAt',
      });
      continue;
    }
    const common = {
      org_id: org,
      id,
      service_account_id: account,
      version: version(d),
      created_at: created,
      updated_at: d.updatedAt ?? created,
    };
    if (grants) {
      const subject = idText(d.subjectUserId);
      const by = idText(d.grantedBy);
      if (!subject || !by) {
        stats.skipped.push({
          id: String(d._id),
          reason: 'delegation without a usable subjectUserId or grantedBy',
        });
        continue;
      }
      rows.push({
        ...common,
        subject_user_id: subject,
        granted_by: by,
        reason: str(d.reason),
      });
    } else if (typeof d.outcome === 'string') {
      rows.push({
        ...common,
        subject_user_id: idText(d.subjectUserId),
        requested_subject:
          typeof d.requestedSubject === 'string' ? d.requestedSubject : '',
        outcome: d.outcome,
        refused_reason: str(d.refusedReason),
        acted_by: idText(d.actedBy),
        note: str(d.note),
        scopes: Array.isArray(d.scopes) ? d.scopes : [],
        access_token_id: idText(d.accessTokenId),
        jti: str(d.jti),
        expires_at: d.expiresAt ?? null,
      });
    } else
      stats.skipped.push({
        id: String(d._id),
        reason: 'delegation event without an outcome',
      });
  }
  await writePlan(
    k,
    [
      {
        table: grants
          ? 'oauth.service_account_delegations'
          : 'oauth.delegation_events',
        rows,
      },
    ],
    stats,
  );
  return stats;
}
