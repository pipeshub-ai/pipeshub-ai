/** Documents of the `documents` collection as rows of `content.documents`. Timestamps are epoch milliseconds in Mongo. */
import type { Tx as KernelTx } from '../../db';
import {
  ImportStats,
  NeutralDoc,
  Row,
  emptyStats,
  idText,
  msToIso,
  writePlan,
} from '../import';

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** The connector a document is tagged with: its customMetadata entry with key connectorId, when the value is text. */
function connectorOf(meta: unknown[]): string | null {
  const e = meta.find(
    (m) => (m as { key?: unknown } | null)?.key === 'connectorId',
  ) as { value?: unknown } | undefined;
  return typeof e?.value === 'string' ? e.value : null;
}

export function documentRow(d: NeutralDoc): Row | null {
  const id = idText(d._id);
  const org = idText(d.orgId);
  if (
    !id ||
    !org ||
    typeof d.documentName !== 'string' ||
    typeof d.storageVendor !== 'string'
  )
    return null;
  const meta = arr(d.customMetadata);
  const created =
    msToIso(d.createdAt) ??
    new Date(parseInt(id.slice(0, 8), 16) * 1000).toISOString();
  const r: Row = {
    org_id: org,
    id,
    document_name: d.documentName,
    alternate_document_name: str(d.alternateDocumentName),
    document_path: str(d.documentPath),
    is_versioned_file: d.isVersionedFile ?? false,
    mutation_count: typeof d.mutationCount === 'number' ? d.mutationCount : 1,
    permissions: str(d.permissions),
    initiator_user_id: idText(d.initiatorUserId),
    // a document created without the field and one created with it set to null answer differently
    initiator_unset: !Object.prototype.hasOwnProperty.call(
      d,
      'initiatorUserId',
    ),
    size_in_bytes:
      typeof d.sizeInBytes === 'number'
        ? String(Math.trunc(d.sizeInBytes))
        : null,
    mime_type: str(d.mimeType),
    extension: str(d.extension),
    version_history: arr(d.versionHistory),
    custom_metadata: meta,
    connector_id: connectorOf(meta),
    current_version:
      typeof d.currentVersion === 'number' ? d.currentVersion : 0,
    tags: arr(d.tags),
    deleted_by_user_id: str(d.deletedByUserId),
    is_deleted: d.isDeleted ?? false,
    s3: d.s3 ?? null,
    azure_blob: d.azureBlob ?? null,
    local: d.local ?? null,
    storage_vendor: d.storageVendor,
    idempotency_key: str(d.idempotencyKey),
    idempotency_fingerprint: str(d.idempotencyFingerprint),
    upload_lease_token: str(d.uploadLeaseToken),
    upload_lease_expires_at: msToIso(d.uploadLeaseExpiresAt),
    awaiting_direct_upload:
      typeof d.awaitingDirectUpload === 'boolean'
        ? d.awaitingDirectUpload
        : null,
    artifact_set: d.artifactSet ?? null,
    retired_at:
      typeof d.retiredAt === 'number' ? String(Math.trunc(d.retiredAt)) : null,
    version: typeof d.__v === 'number' ? d.__v : 0,
    created_at: created,
    updated_at: msToIso(d.updatedAt) ?? created,
  };
  return r;
}

export async function importDocumentDocs(
  k: KernelTx,
  docs: NeutralDoc[],
): Promise<ImportStats> {
  const stats = emptyStats(docs.length);
  const rows: Row[] = [];
  for (const d of docs) {
    const r = documentRow(d);
    if (r) rows.push(r);
    else
      stats.skipped.push({
        id: String(d._id),
        reason:
          'document without a usable _id, orgId, documentName or storageVendor',
      });
  }
  await writePlan(k, [{ table: 'content.documents', rows }], stats);
  return stats;
}
