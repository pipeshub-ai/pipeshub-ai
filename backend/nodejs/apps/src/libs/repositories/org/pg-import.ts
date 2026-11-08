/** Mongo documents of the `org` and `org-logos` collections as rows of `identity.orgs` and `identity.org_logos`. */
import {
  ImportPlan,
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  writePlan,
} from '../import';
import type { Tx as KernelTx } from '../../db';
import { importCounterDocs } from '../slug/pg-import';

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);

/** The columns a document names; a column left out takes the table's default (the slug comes from the sequence). */
export function orgRow(d: NeutralDoc): Row {
  const id = idText(d._id);
  if (!id)
    throw new Error(`organisation _id '${String(d._id)}' is not an ObjectId`);
  const r: Row = {
    org_id: id,
    registered_name: str(d.registeredName),
    short_name: str(d.shortName),
    domain: d.domain,
    contact_email: d.contactEmail,
    account_type: d.accountType,
    phone_number: str(d.phoneNumber),
    permanent_address: d.permanentAddress ?? null,
    on_boarding_status: str(d.onBoardingStatus),
    is_deleted: d.isDeleted ?? false,
    deleted_by_user: str(d.deletedByUser),
    admin_role_guard_at: d.adminRoleGuardAt ?? null,
    signup_channel: str(d.signupChannel),
    signup_referrer_domain: str(d.signupReferrerDomain),
    version: typeof d.__v === 'number' ? d.__v : 0,
    created_at: d.createdAt,
    updated_at: d.updatedAt ?? d.createdAt,
  };
  if (typeof d.slug === 'string') r.slug = d.slug;
  return r;
}

export function orgLogoRow(d: NeutralDoc): Row | null {
  const id = idText(d._id);
  const org = idText(d.orgId);
  if (!id || !org) return null;
  return {
    org_id: org,
    id,
    logo: str(d.logo),
    logo_url: str(d.logoUrl),
    mime_type: str(d.mimeType),
    version: typeof d.__v === 'number' ? d.__v : 0,
  };
}

export async function importOrgDocs(
  k: KernelTx,
  collection: string,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  if (collection === 'counters') return importCounterDocs(k, docs);
  const stats = emptyStats(docs.length);
  const plan: ImportPlan = [];
  if (collection === 'org') {
    plan.push({ table: 'identity.orgs', rows: docs.map(orgRow) });
  } else if (collection === 'org-logos') {
    const rows: Row[] = [];
    for (const d of docs) {
      const r = orgLogoRow(d);
      if (r) rows.push(r);
      else
        stats.skipped.push({
          id: String(d._id),
          reason: 'org-logos document without a usable orgId',
        });
    }
    plan.push({ table: 'identity.org_logos', rows });
  } else
    throw new Error(
      `the organisation repository does not import '${collection}'`,
    );
  await writePlan(k, plan, stats);
  return stats;
}
