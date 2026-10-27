/**
 * PostgreSQL implementation of the document repository (`DOC_STORE=postgres`). A call that names no org (a lookup by row id) runs
 * for the org of the unit it joins, so a row of another org is not found. Paths compare as bytes (the column is COLLATE "C"), which
 * makes "path and everything beneath it" a range: `>= path/` and `< path0`.
 */
import {
  BadRequestError,
  ConflictError,
  InternalServerError,
} from '../../errors/http.errors';
import { stripNul } from '../nul';
import {
  Params,
  assertOrg,
  fill,
  inUnit,
  newObjectId,
  oidOf,
  oidsOf,
} from '../pg-util';
import type { Tx as PgKernelTx } from '../../db';
import type { StorageVendor } from '../../../modules/storage/types/storage.service.types';
import type {
  ArtifactFence,
  ConnectorScope,
  DocumentPatch,
  DocumentRepository,
  DocumentRow,
  DocumentTreeRow,
  IdempotencyClaim,
  NewDocument,
  UpdateOptions,
} from './types';
import { ENVELOPE_PREFIXES } from './types';

interface Row {
  id: string;
  org_id: string;
  document_name: string;
  alternate_document_name: string | null;
  document_path: string | null;
  is_versioned_file: boolean;
  mutation_count: number;
  permissions: string | null;
  initiator_user_id: string | null;
  size_in_bytes: string | null;
  mime_type: string | null;
  extension: string | null;
  version_history: unknown[];
  initiator_unset: boolean;
  custom_metadata: unknown[];
  current_version: number;
  tags: string[];
  deleted_by_user_id: string | null;
  is_deleted: boolean;
  s3: unknown;
  azure_blob: unknown;
  local: unknown;
  storage_vendor: string;
  idempotency_key: string | null;
  idempotency_fingerprint: string | null;
  upload_lease_token: string | null;
  upload_lease_expires_at: Date | null;
  awaiting_direct_upload: boolean | null;
  artifact_set: unknown;
  retired_at: string | null;
  version: number;
  created_at: Date;
  updated_at: Date;
}

const COLS = `id, org_id, document_name, alternate_document_name, document_path, is_versioned_file, mutation_count, permissions,
  initiator_user_id, initiator_unset, size_in_bytes, mime_type, extension, version_history, custom_metadata, current_version, tags, deleted_by_user_id,
  is_deleted, s3, azure_blob, local, storage_vendor, idempotency_key, idempotency_fingerprint, upload_lease_token,
  upload_lease_expires_at, awaiting_direct_upload, artifact_set, retired_at, version, created_at, updated_at`;
const TREE_COLS =
  'id, document_path, document_name, extension, is_versioned_file, version_history';
const NO_INITIATOR = '000000000000000000000000';

/** The column that holds each vendor's storage info. */
const VENDOR_COLUMN: Record<string, string> = {
  s3: 's3',
  azureBlob: 'azure_blob',
  local: 'local',
};
/** The dotted names `setFields` is called with: a key inside a storage info document. */
const DOTTED: Record<string, [string, string]> = {
  'local.localPath': ['local', 'localPath'],
  'azureBlob.url': ['azure_blob', 'url'],
  's3.url': ['s3', 'url'],
};

const ms = (d: Date | null): number | undefined =>
  d ? d.getTime() : undefined;

function docOf(r: Row): DocumentRow {
  const o: Record<string, unknown> = {
    _id: r.id,
    orgId: r.org_id,
    documentName: r.document_name,
    isVersionedFile: r.is_versioned_file,
    mutationCount: r.mutation_count,
    versionHistory: r.version_history,
    customMetadata: r.custom_metadata,
    currentVersion: r.current_version,
    tags: r.tags,
    createdAt: r.created_at.getTime(),
    updatedAt: r.updated_at.getTime(),
    isDeleted: r.is_deleted,
    storageVendor: r.storage_vendor,
    __v: r.version,
  };
  const optional: Array<[string, unknown]> = [
    ['alternateDocumentName', r.alternate_document_name],
    ['documentPath', r.document_path],
    [
      'initiatorUserId',
      r.initiator_user_id ?? (r.initiator_unset ? undefined : null),
    ],
    ['permissions', r.permissions],
    ['sizeInBytes', r.size_in_bytes === null ? null : Number(r.size_in_bytes)],
    ['mimeType', r.mime_type],
    ['extension', r.extension],
    ['deletedByUserId', r.deleted_by_user_id],
    ['s3', r.s3],
    ['azureBlob', r.azure_blob],
    ['local', r.local],
    ['idempotencyKey', r.idempotency_key],
    ['idempotencyFingerprint', r.idempotency_fingerprint],
    ['uploadLeaseToken', r.upload_lease_token],
    ['uploadLeaseExpiresAt', ms(r.upload_lease_expires_at) ?? null],
    ['awaitingDirectUpload', r.awaiting_direct_upload],
    ['artifactSet', r.artifact_set],
    ['retiredAt', r.retired_at === null ? null : Number(r.retired_at)],
  ];
  for (const [key, v] of optional)
    if (v !== undefined && (v !== null || key === 'initiatorUserId'))
      o[key] = v;
  return o as unknown as DocumentRow;
}

function treeOf(r: Partial<Row>): DocumentTreeRow {
  const o: Record<string, unknown> = {
    _id: r.id,
    documentName: r.document_name,
    isVersionedFile: r.is_versioned_file,
  };
  if (r.document_path !== null && r.document_path !== undefined)
    o.documentPath = r.document_path;
  if (r.extension !== null && r.extension !== undefined)
    o.extension = r.extension;
  if (r.version_history !== undefined) o.versionHistory = r.version_history;
  if (r.custom_metadata !== undefined) o.customMetadata = r.custom_metadata;
  if (r.is_deleted !== undefined) o.isDeleted = r.is_deleted;
  return o as unknown as DocumentTreeRow;
}

/** The connector a row is tagged with: the customMetadata entry with key connectorId, when its value is text. */
const VERSION_FIELDS = [
  'version',
  'userAssignedVersionLabel',
  'note',
  'extension',
  'currentVersion',
  'createdAt',
  'initiatedByUserId',
  'size',
] as const;
const keep = (o: unknown, fields: readonly string[]): Record<string, unknown> =>
  Object.fromEntries(
    fields
      .filter((f) => (o as Record<string, unknown> | null)?.[f] !== undefined)
      .map((f) => [f, (o as Record<string, unknown>)[f]]),
  );

/** What the document schema keeps of a version entry: any other key was dropped on write by the Mongo model. */
function versionsOf(list: unknown): unknown[] {
  if (!Array.isArray(list)) return [];
  return list.map((v: Record<string, unknown> | null) => {
    const out = keep(v, VERSION_FIELDS);
    if (v?.s3) out.s3 = keep(v.s3, ['url']);
    if (v?.azureBlob) out.azureBlob = keep(v.azureBlob, ['url']);
    if (v?.local) out.local = keep(v.local, ['url', 'localPath']);
    return out;
  });
}

/** A string field the schema trims; unset stays unset. */
const trimmed = (v: string | null | undefined): string | null =>
  typeof v === 'string' ? v.trim() : null;

/** Storage info keeps the fields its sub-schema names. */
const storageInfo = (v: unknown, fields: readonly string[]): unknown =>
  v === undefined || v === null ? v : keep(v, fields);

/** Custom metadata entries keep only their key and value. */
const metadataOf = (list: unknown): unknown[] =>
  Array.isArray(list) ? list.map((e) => keep(e, ['key', 'value'])) : [];

function connectorOf(customMetadata: unknown): string | null {
  if (!Array.isArray(customMetadata)) return null;
  const e = (customMetadata as Array<{ key?: unknown; value?: unknown }>).find(
    (m) => m.key === 'connectorId',
  );
  return typeof e?.value === 'string' ? e.value : null;
}

const json = (v: unknown): string | null =>
  v === undefined || v === null ? null : JSON.stringify(v);

function oidOrNull(v: unknown, what: string): string | null {
  if (v === undefined || v === null) return null;
  const id = oidOf(v);
  if (!id) throw new BadRequestError(`${what} is not a valid id`);
  return id;
}

/** The `SET` entries for a patch or a `setFields` set. A name this store does not know is refused, never skipped. */
function setClauses(p: Params, set: Record<string, unknown>): string[] {
  const out: string[] = [];
  for (const [key, v] of Object.entries(set)) {
    if (DOTTED[key]) {
      const [col, inner] = DOTTED[key];
      out.push(
        `${col} = jsonb_set(COALESCE(${col}, '{}'::jsonb), '{${inner}}', to_jsonb(${p.add(v)}::text))`,
      );
      continue;
    }
    switch (key) {
      case 'documentPath':
        out.push(`document_path = ${p.add(trimmed(v as string | undefined))}`);
        break;
      case 'mutationCount':
        out.push(`mutation_count = ${p.add(v ?? 1)}`);
        break;
      case 'sizeInBytes':
        out.push(`size_in_bytes = ${p.add(v ?? null)}`);
        break;
      case 's3':
        out.push(`s3 = ${p.add(json(storageInfo(v, ['url'])))}::jsonb`);
        break;
      case 'azureBlob':
        out.push(`azure_blob = ${p.add(json(storageInfo(v, ['url'])))}::jsonb`);
        break;
      case 'local':
        out.push(
          `local = ${p.add(json(storageInfo(v, ['url', 'localPath'])))}::jsonb`,
        );
        break;
      case 'isDeleted':
        out.push(`is_deleted = ${p.add(v ?? false)}`);
        break;
      case 'deletedByUserId':
        out.push(`deleted_by_user_id = ${p.add(v ?? null)}`);
        break;
      case 'awaitingDirectUpload':
        out.push(`awaiting_direct_upload = ${p.add(v ?? null)}`);
        break;
      case 'versionHistory':
        out.push(`version_history = ${p.add(json(versionsOf(v)))}::jsonb`);
        break;
      case 'customMetadata':
        out.push(
          `custom_metadata = ${p.add(json(metadataOf(v)))}::jsonb`,
          `connector_id = ${p.add(connectorOf(v))}`,
        );
        break;
      default:
        throw new InternalServerError(
          `document field '${key}' cannot be written by this store`,
        );
    }
  }
  return out;
}

/** Rows filed at `prefix` or beneath it, as a SQL condition on document_path. */
function underPrefix(p: Params, prefix: string): string {
  return `(document_path = ${p.add(prefix)} OR (document_path >= ${p.add(`${prefix}/`)} AND document_path < ${p.add(`${prefix}0`)}))`;
}

function connectorCondition(
  p: Params,
  scope: ConnectorScope,
): { tagged: string; under: string } {
  const tagged = `connector_id = ${p.add(scope.connectorId)}`;
  const under = scope.prefixes.length
    ? `(${scope.prefixes.map((x) => underPrefix(p, x)).join(' OR ')})`
    : 'false';
  return { tagged, under };
}

async function insert(
  k: PgKernelTx,
  input: NewDocument,
  keyed: boolean,
): Promise<DocumentRow | null> {
  assertOrg(k, String(input.orgId));
  const i = stripNul(input, 'document', 'row');
  const created = new Date(i.createdAt ?? Date.now());
  const p = new Params();
  const values: Array<[string, unknown]> = [
    ['org_id', k.orgId],
    ['id', newObjectId()],
    ['document_name', i.documentName?.trim()],
    ['alternate_document_name', trimmed(i.alternateDocumentName)],
    ['document_path', trimmed(i.documentPath)],
    ['is_versioned_file', i.isVersionedFile ?? false],
    ['mutation_count', i.mutationCount ?? 1],
    ['permissions', i.permissions ?? null],
    ['initiator_user_id', oidOrNull(i.initiatorUserId, 'initiatorUserId')],
    ['initiator_unset', i.initiatorUserId === undefined],
    ['size_in_bytes', i.sizeInBytes ?? null],
    ['mime_type', i.mimeType ?? null],
    ['extension', i.extension ?? null],
    ['version_history', json(versionsOf(i.versionHistory))],
    ['custom_metadata', json(metadataOf(i.customMetadata))],
    ['connector_id', connectorOf(i.customMetadata)],
    ['current_version', i.currentVersion ?? 0],
    ['tags', (i.tags ?? []).map((t) => t.trim())],
    ['deleted_by_user_id', i.deletedByUserId ?? null],
    ['is_deleted', i.isDeleted ?? false],
    ['s3', json(storageInfo(i.s3, ['url']))],
    ['azure_blob', json(storageInfo(i.azureBlob, ['url']))],
    ['local', json(storageInfo(i.local, ['url', 'localPath']))],
    ['storage_vendor', i.storageVendor],
    ['idempotency_key', i.idempotencyKey ?? null],
    ['idempotency_fingerprint', i.idempotencyFingerprint ?? null],
    ['upload_lease_token', i.uploadLeaseToken ?? null],
    [
      'upload_lease_expires_at',
      i.uploadLeaseExpiresAt === undefined
        ? null
        : new Date(i.uploadLeaseExpiresAt),
    ],
    ['awaiting_direct_upload', i.awaitingDirectUpload ?? null],
    [
      'artifact_set',
      json(storageInfo(i.artifactSet, ['virtualRecordId', 'rev', 'root'])),
    ],
    ['retired_at', i.retiredAt ?? null],
    ['created_at', created],
    ['updated_at', new Date(i.updatedAt ?? created.getTime())],
  ];
  const jsonb = new Set([
    'version_history',
    'custom_metadata',
    's3',
    'azure_blob',
    'local',
    'artifact_set',
  ]);
  const marks = values.map(([c, v]) =>
    jsonb.has(c) ? `${p.add(v)}::jsonb` : p.add(v),
  );
  const { rows } = await k.query<Row>(
    fill(
      `/* pipeshub:content.document_insert */ INSERT INTO content.documents (@{A})
       VALUES (@{B})@{C} RETURNING @{COLS}`,
      {
        A: values.map(([c]) => c).join(', '),
        B: marks.join(', '),
        C: keyed ? ' ON CONFLICT DO NOTHING' : '',
        COLS,
      },
    ),
    p.values,
  );
  return rows[0] ? docOf(rows[0]) : null;
}

export class PgDocumentRepository implements DocumentRepository {
  async get(orgId: string, id: string): Promise<DocumentRow | null> {
    return this.read(orgId, id, false);
  }

  async getLive(
    orgId: string,
    id: string,
    _lean?: boolean,
  ): Promise<DocumentRow | null> {
    return this.read(orgId, id, true);
  }

  private async read(
    orgId: string,
    id: string,
    live: boolean,
  ): Promise<DocumentRow | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.document_get */ SELECT @{COLS} FROM content.documents WHERE org_id = $1 AND id = $2@{B}`,
          { COLS, B: live ? ' AND NOT is_deleted' : '' },
        ),
        [k.orgId, oid],
      );
      return rows[0] ? docOf(rows[0]) : null;
    });
  }

  async create(input: NewDocument): Promise<DocumentRow> {
    return inUnit(
      undefined,
      { write: true },
      async (k) => (await insert(k, input, false)) as DocumentRow,
    );
  }

  async createIdempotent(
    input: NewDocument,
    idempotency: IdempotencyClaim,
    firstAttemptFields: NewDocument,
  ): Promise<{ document: DocumentRow; replayed: boolean }> {
    return inUnit(undefined, { write: true }, async (k) => {
      const keyed: NewDocument = {
        ...input,
        ...firstAttemptFields,
        idempotencyKey: idempotency.key,
        idempotencyFingerprint: idempotency.fingerprint,
      };
      const made = await insert(k, keyed, true);
      if (made) return { document: made, replayed: false };
      // lost to the attempt that holds the key: its row is the answer (the conflicting insert waited for its commit)
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.document_by_key */ SELECT @{COLS} FROM content.documents
          WHERE org_id = $1 AND COALESCE(initiator_user_id, '@{NO_INITIATOR}') = $2 AND idempotency_key = $3`,
          { COLS, NO_INITIATOR },
        ),
        [
          k.orgId,
          oidOrNull(input.initiatorUserId, 'initiatorUserId') ?? NO_INITIATOR,
          idempotency.key,
        ],
      );
      if (!rows[0])
        throw new InternalServerError(
          'the document that holds this idempotency key cannot be read',
        );
      return { document: docOf(rows[0]), replayed: true };
    });
  }

  async update(
    orgId: string,
    id: string,
    patch: DocumentPatch,
    opts: UpdateOptions = {},
  ): Promise<DocumentRow | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const org = p.add(k.orgId);
      const rowId = p.add(oid);
      const sets = setClauses(
        p,
        stripNul(patch as Record<string, unknown>, 'document', 'patch'),
      );
      if (opts.pushVersions?.length) {
        sets.push(
          `version_history = version_history || ${p.add(json(versionsOf(stripNul(opts.pushVersions, 'document', 'version'))))}::jsonb`,
          'version = version + 1',
        );
      }
      let guard = '';
      if (opts.holdLease !== undefined) {
        sets.push(
          'upload_lease_token = NULL',
          'upload_lease_expires_at = NULL',
        );
        guard = ` AND upload_lease_token = ${p.add(opts.holdLease)}`;
      }
      sets.push(`updated_at = ${p.add(new Date())}`);
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.document_update */ UPDATE content.documents SET @{A}
          WHERE org_id = @{B} AND id = @{C}@{D} RETURNING @{COLS}`,
          { A: sets.join(', '), B: org, C: rowId, D: guard, COLS },
        ),
        p.values,
      );
      return rows[0] ? docOf(rows[0]) : null;
    });
  }

  async remove(id: string, orgId?: string): Promise<void> {
    const oid = oidOf(id);
    if (!oid) return;
    await inUnit(undefined, { write: true }, async (k) => {
      if (orgId !== undefined) assertOrg(k, orgId);
      await k.query(
        '/* pipeshub:content.document_remove */ DELETE FROM content.documents WHERE org_id = $1 AND id = $2',
        [k.orgId, oid],
      );
    });
  }

  async removeUnstored(id: string, vendor: StorageVendor): Promise<void> {
    const oid = oidOf(id);
    const col = VENDOR_COLUMN[vendor];
    if (!oid || !col) return;
    await inUnit(undefined, { write: true }, async (k) => {
      await k.query(
        fill(
          `/* pipeshub:content.document_remove_unstored */ DELETE FROM content.documents WHERE org_id = $1 AND id = $2 AND @{A} IS NULL`,
          { A: col },
        ),
        [k.orgId, oid],
      );
    });
  }

  async removeAwaitingDirectUpload(
    orgId: string,
    id: string,
  ): Promise<boolean> {
    const oid = oidOf(id);
    if (!oid) return false;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const r = await k.query(
        '/* pipeshub:content.document_remove_awaiting */ DELETE FROM content.documents WHERE org_id = $1 AND id = $2 AND awaiting_direct_upload = true',
        [k.orgId, oid],
      );
      return r.rowCount === 1;
    });
  }

  async clearAwaitingDirectUpload(orgId: string, id: string): Promise<void> {
    const oid = oidOf(id);
    if (!oid) return;
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      await k.query(
        '/* pipeshub:content.document_clear_awaiting */ UPDATE content.documents SET awaiting_direct_upload = NULL WHERE org_id = $1 AND id = $2',
        [k.orgId, oid],
      );
    });
  }

  async claimUpload(
    id: string,
    vendor: StorageVendor,
    leaseToken: string,
    leaseMs: number,
  ): Promise<DocumentRow | null> {
    const oid = oidOf(id);
    const col = VENDOR_COLUMN[vendor];
    if (!oid || !col) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      const now = Date.now();
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.document_claim_upload */ UPDATE content.documents
            SET upload_lease_token = $3, upload_lease_expires_at = $4, updated_at = $5
          WHERE org_id = $1 AND id = $2 AND @{A} IS NULL AND (upload_lease_expires_at IS NULL OR upload_lease_expires_at <= $5)
          RETURNING @{COLS}`,
          { A: col, COLS },
        ),
        [k.orgId, oid, leaseToken, new Date(now + leaseMs), new Date(now)],
      );
      return rows[0] ? docOf(rows[0]) : null;
    });
  }

  async releaseUpload(id: string, leaseToken: string): Promise<void> {
    const oid = oidOf(id);
    if (!oid) return;
    await inUnit(undefined, { write: true }, async (k) => {
      await k.query(
        `/* pipeshub:content.document_release_upload */ UPDATE content.documents SET upload_lease_token = NULL, upload_lease_expires_at = NULL
          WHERE org_id = $1 AND id = $2 AND upload_lease_token = $3`,
        [k.orgId, oid, leaseToken],
      );
    });
  }

  async ensureIndexes(): Promise<void> {
    // the unique index comes with the migration
  }

  async listByVirtualRecord(
    orgId: string,
    virtualRecordId: string,
    flatPath: string,
  ): Promise<DocumentRow[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.document_by_virtual_record */ SELECT @{COLS} FROM content.documents
          WHERE org_id = $1 AND (document_path = $2 OR document_name = ANY($3::text[]) OR artifact_set->>'virtualRecordId' = $4)`,
          { COLS },
        ),
        [
          k.orgId,
          flatPath,
          ENVELOPE_PREFIXES.map((x) => `${x}${virtualRecordId}`),
          virtualRecordId,
        ],
      );
      return rows.map(docOf);
    });
  }

  async listForRelocate(
    orgId: string,
    virtualRecordId: string,
    flatPath: string,
  ): Promise<DocumentTreeRow[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.document_for_relocate */ SELECT @{TREE_COLS}, custom_metadata FROM content.documents
          WHERE org_id = $1 AND artifact_set IS NULL AND (document_path = $2 OR document_name = ANY($3::text[]))`,
          { TREE_COLS },
        ),
        [
          k.orgId,
          flatPath,
          ENVELOPE_PREFIXES.map((x) => `${x}${virtualRecordId}`),
        ],
      );
      return rows.map(treeOf);
    });
  }

  async listEnvelopes(
    orgId: string,
    virtualRecordId: string,
    opts?: { olderThanSeconds?: number },
  ): Promise<
    {
      _id: string;
      documentName?: string;
      documentPath?: string;
      createdAt?: number;
      retiredAt?: number | null;
    }[]
  > {
    const olderThanSeconds = opts?.olderThanSeconds;
    const aged =
      olderThanSeconds !== undefined &&
      Number.isFinite(olderThanSeconds) &&
      olderThanSeconds >= 0;
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const org = p.add(k.orgId);
      const names = p.add(
        ENVELOPE_PREFIXES.map((x) => `${x}${virtualRecordId}`),
      );
      // The database's own clock, as the MongoDB implementation uses.
      const age = aged
        ? ` AND created_at < now() - make_interval(secs => ${p.add(olderThanSeconds)}::double precision)`
        : '';
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.document_envelopes */ SELECT id, document_name, document_path, created_at, retired_at FROM content.documents
          WHERE org_id = @{A} AND document_name = ANY(@{B}::text[]) AND NOT is_deleted@{C} ORDER BY created_at DESC, id DESC`,
          { A: org, B: names, C: age },
        ),
        p.values,
      );
      return rows.map((r) => ({
        _id: r.id,
        documentName: r.document_name,
        ...(r.document_path === null ? {} : { documentPath: r.document_path }),
        createdAt: r.created_at.getTime(),
        retiredAt: r.retired_at === null ? null : Number(r.retired_at),
      }));
    });
  }

  async retire(
    orgId: string,
    id: string,
    now: number,
  ): Promise<{ retiredAt: number | null } | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      // The first mark's time is kept; updated_at is left alone, as the retire is not an edit.
      const marked = await k.query<Pick<Row, 'retired_at'>>(
        `/* pipeshub:content.document_retire */ UPDATE content.documents SET retired_at = $3
          WHERE org_id = $1 AND id = $2 AND NOT is_deleted AND retired_at IS NULL RETURNING retired_at`,
        [k.orgId, oid, now],
      );
      const row =
        marked.rows[0] ??
        (
          await k.query<Pick<Row, 'retired_at'>>(
            '/* pipeshub:content.document_retired_at */ SELECT retired_at FROM content.documents WHERE org_id = $1 AND id = $2 AND NOT is_deleted',
            [k.orgId, oid],
          )
        ).rows[0];
      return row
        ? { retiredAt: row.retired_at === null ? null : Number(row.retired_at) }
        : null;
    });
  }

  async pageFlatEnvelopes(
    orgId: string,
    flatPrefix: string,
    after: string | undefined,
    limit: number,
  ): Promise<{ _id: string; documentName?: string; documentPath?: string }[]> {
    const afterId = after === undefined ? undefined : oidOf(after);
    if (after !== undefined && !afterId) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const org = p.add(k.orgId);
      const prefix = p.add(flatPrefix);
      // document_path = "<prefix><vrid>", vrid being the name after "record_" / "metadata_" (at most 200 characters).
      const flat = ENVELOPE_PREFIXES.map(
        (x) =>
          `(starts_with(document_name, ${p.add(x)}) AND document_path = ${prefix}::text || substr(document_name, ${x.length + 1}, 200))`,
      ).join(' OR ');
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.document_flat_envelopes */ SELECT id, document_name, document_path FROM content.documents
          WHERE org_id = @{B} AND NOT is_deleted AND (@{C})@{D} ORDER BY id LIMIT @{E}`,
          {
            B: org,
            C: flat,
            D: afterId === undefined ? '' : ` AND id > ${p.add(afterId)}`,
            E: p.add(limit),
          },
        ),
        p.values,
      );
      return rows.map((r) => ({
        _id: r.id,
        documentName: r.document_name,
        ...(r.document_path === null ? {} : { documentPath: r.document_path }),
      }));
    });
  }

  async listByFolders(
    orgId: string,
    folderPaths: string[],
  ): Promise<DocumentRow[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.document_by_folders */ SELECT @{COLS} FROM content.documents WHERE org_id = $1 AND document_path = ANY($2::text[])`,
          { COLS },
        ),
        [k.orgId, folderPaths],
      );
      return rows.map(docOf);
    });
  }

  async findExistingIds(orgId: string, ids: string[]): Promise<string[]> {
    const wanted = oidsOf(ids);
    if (!wanted.length) return [];
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<{ id: string }>(
        '/* pipeshub:content.document_existing_ids */ SELECT id FROM content.documents WHERE org_id = $1 AND id = ANY($2::text[]) AND NOT is_deleted',
        [k.orgId, wanted],
      );
      return rows.map((r) => r.id);
    });
  }

  async subtree(orgId: string, path: string): Promise<DocumentTreeRow[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const org = p.add(k.orgId);
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.document_subtree */ SELECT @{TREE_COLS}, is_deleted FROM content.documents WHERE org_id = @{B} AND @{C}`,
          { TREE_COLS, B: org, C: underPrefix(p, path) },
        ),
        p.values,
      );
      return rows.map(treeOf);
    });
  }

  async rewriteTree(
    oldPath: string,
    newPath: string,
    rows: { id: string; set: Record<string, unknown> }[],
  ): Promise<void> {
    const listed = rows
      .map((r) => ({ id: oidOf(r.id), set: r.set }))
      .filter(
        (r): r is { id: string; set: Record<string, unknown> } =>
          r.id !== undefined,
      );
    if (!listed.length) return;
    await inUnit(undefined, { write: true }, async (k) => {
      const now = new Date();
      await k.query(
        `/* pipeshub:content.document_rewrite_tree */ UPDATE content.documents
            SET document_path = $3 || substr(document_path, char_length($4) + 1), updated_at = $5
          WHERE org_id = $1 AND id = ANY($2::text[])`,
        [k.orgId, listed.map((r) => r.id), newPath, oldPath, now],
      );
      for (const r of listed) await this.setIn(k, r.id, r.set);
    });
  }

  async setFields(id: string, set: Record<string, unknown>): Promise<void> {
    const oid = oidOf(id);
    if (!oid) return;
    await inUnit(undefined, { write: true }, (k) => this.setIn(k, oid, set));
  }

  async setFieldsMany(
    rows: { id: string; set: Record<string, unknown> }[],
  ): Promise<void> {
    const listed = rows
      .map((r) => ({ id: oidOf(r.id), set: r.set }))
      .filter(
        (r): r is { id: string; set: Record<string, unknown> } =>
          r.id !== undefined,
      );
    if (!listed.length) return;
    await inUnit(undefined, { write: true }, async (k) => {
      for (const r of listed) await this.setIn(k, r.id, r.set);
    });
  }

  async createArtifactSetOnce(
    input: NewDocument,
    fence: ArtifactFence,
  ): Promise<DocumentRow> {
    return inUnit(undefined, { write: true }, async (k) => {
      const touched = await this.touchRevision(k, input, fence);
      if (touched) return touched;
      const made = await insert(k, input, true);
      if (made) return made;
      // lost to the attempt that made this revision first
      const winner = await this.touchRevision(k, input, fence);
      if (!winner)
        throw new InternalServerError(
          'the artifact set that holds this revision cannot be read',
        );
      return winner;
    });
  }

  /**
   * Moves the revision's write stamp and returns it, or null when there is no such revision. A claimed revision is refused; a claim
   * past the stale bound is from a delete that died and is dropped here.
   */
  private async touchRevision(
    k: PgKernelTx,
    input: NewDocument,
    { now, staleMs }: ArtifactFence,
  ): Promise<DocumentRow | null> {
    assertOrg(k, String(input.orgId));
    const key = [
      k.orgId,
      input.artifactSet?.virtualRecordId,
      input.artifactSet?.rev,
    ];
    const { rows } = await k.query<Row>(
      fill(
        `/* pipeshub:content.document_artifact_touch */ UPDATE content.documents
        SET updated_at = $4, artifact_set = artifact_set - 'retiringAt'
        WHERE org_id = $1 AND artifact_set->>'virtualRecordId' = $2 AND artifact_set->>'rev' = $3
          AND (NOT artifact_set ? 'retiringAt' OR (artifact_set->>'retiringAt')::bigint < $5)
        RETURNING @{COLS}`,
        { COLS },
      ),
      [...key, new Date(now), now - staleMs],
    );
    if (rows[0]) return docOf(rows[0]);
    const { rowCount } = await k.query(
      `/* pipeshub:content.document_artifact_present */ SELECT 1 FROM content.documents
      WHERE org_id = $1 AND artifact_set->>'virtualRecordId' = $2 AND artifact_set->>'rev' = $3`,
      key,
    );
    if ((rowCount ?? 0) > 0)
      throw new ConflictError(
        'This revision is being deleted; write it again once the delete has finished',
      );
    return null;
  }

  async claimArtifactSetForDelete(
    orgId: string,
    id: string,
    {
      now,
      staleMs,
      ifUnchangedSince,
    }: { now: number; staleMs: number; ifUnchangedSince?: number },
  ): Promise<DocumentRow | 'changed' | null> {
    const oid = oidOf(id);
    if (!oid) return null;
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      // updated_at is left alone: the claim is not a write of the revision, and a delete retried after a crash must still match the
      // listed stamp. Without a stamp the delete is unfenced: it claims whatever is there.
      const { rows } = await (ifUnchangedSince === undefined
        ? k.query<Row>(
            fill(
              `/* pipeshub:content.document_artifact_claim */ UPDATE content.documents
              SET artifact_set = jsonb_set(artifact_set, '{retiringAt}', to_jsonb($3::bigint))
              WHERE org_id = $1 AND id = $2 AND artifact_set IS NOT NULL
              RETURNING @{COLS}`,
              { COLS },
            ),
            [k.orgId, oid, now],
          )
        : k.query<Row>(
            fill(
              `/* pipeshub:content.document_artifact_claim_fenced */ UPDATE content.documents
              SET artifact_set = jsonb_set(artifact_set, '{retiringAt}', to_jsonb($3::bigint))
              WHERE org_id = $1 AND id = $2 AND artifact_set IS NOT NULL AND updated_at <= $4
                AND (NOT artifact_set ? 'retiringAt' OR (artifact_set->>'retiringAt')::bigint < $5)
              RETURNING @{COLS}`,
              { COLS },
            ),
            [k.orgId, oid, now, new Date(ifUnchangedSince), now - staleMs],
          ));
      if (rows[0]) return docOf(rows[0]);
      if (ifUnchangedSince === undefined) return null;
      const present = await k.query(
        '/* pipeshub:content.document_artifact_claim_present */ SELECT 1 FROM content.documents WHERE org_id = $1 AND id = $2 AND artifact_set IS NOT NULL',
        [k.orgId, oid],
      );
      return (present.rowCount ?? 0) > 0 ? 'changed' : null;
    });
  }

  async renewArtifactSetClaim(
    orgId: string,
    id: string,
    known: number[],
    next: number,
  ): Promise<void> {
    const oid = oidOf(id);
    if (!oid) return;
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      await k.query(
        `/* pipeshub:content.document_artifact_renew */ UPDATE content.documents
        SET artifact_set = jsonb_set(artifact_set, '{retiringAt}', to_jsonb($3::bigint))
        WHERE org_id = $1 AND id = $2 AND (artifact_set->>'retiringAt')::bigint = ANY($4::bigint[])`,
        [k.orgId, oid, next, known],
      );
    });
  }

  async removeClaimedArtifactSet(
    id: string,
    orgId: string,
    claims: number[],
  ): Promise<void> {
    const oid = oidOf(id);
    if (!oid) return;
    await inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      await k.query(
        `/* pipeshub:content.document_artifact_remove_claimed */ DELETE FROM content.documents
        WHERE org_id = $1 AND id = $2 AND (artifact_set->>'retiringAt')::bigint = ANY($3::bigint[])`,
        [k.orgId, oid, claims],
      );
    });
  }

  async listArtifactSets(
    orgId: string,
    virtualRecordId: string,
  ): Promise<DocumentRow[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.document_artifact_sets */ SELECT @{COLS} FROM content.documents
          WHERE org_id = $1 AND artifact_set->>'virtualRecordId' = $2 AND NOT is_deleted ORDER BY created_at, id`,
          { COLS },
        ),
        [k.orgId, virtualRecordId],
      );
      return rows.map(docOf);
    });
  }

  private async setIn(
    k: PgKernelTx,
    id: string,
    set: Record<string, unknown>,
  ): Promise<void> {
    const p = new Params();
    const org = p.add(k.orgId);
    const rowId = p.add(id);
    const sets = setClauses(p, stripNul(set, 'document', 'fields'));
    if (!sets.length) return;
    sets.push(`updated_at = ${p.add(new Date())}`);
    await k.query(
      fill(
        `/* pipeshub:content.document_set_fields */ UPDATE content.documents SET @{A} WHERE org_id = @{B} AND id = @{C}`,
        { A: sets.join(', '), B: org, C: rowId },
      ),
      p.values,
    );
  }

  async pageByConnector(
    orgId: string,
    scope: ConnectorScope,
    after: string | undefined,
    limit: number,
  ): Promise<{ _id: string; documentName?: string; documentPath?: string }[]> {
    return inUnit(undefined, { write: false }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const org = p.add(k.orgId);
      const { tagged, under } = connectorCondition(p, scope);
      const afterId = after === undefined ? undefined : (oidOf(after) ?? '');
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:content.document_page_by_connector */ SELECT id, document_name, document_path FROM content.documents
          WHERE org_id = @{A} AND (@{B} OR @{C})@{D}
          ORDER BY id LIMIT @{E}`,
          {
            A: org,
            B: tagged,
            C: under,
            D: afterId === undefined ? '' : ` AND id > ${p.add(afterId)}`,
            E: p.add(limit),
          },
        ),
        p.values,
      );
      return rows.map((r) => ({
        _id: r.id,
        documentName: r.document_name,
        ...(r.document_path === null ? {} : { documentPath: r.document_path }),
      }));
    });
  }

  async *streamTaggedOutsidePrefixes(
    orgId: string,
    scope: ConnectorScope,
  ): AsyncIterable<{ _id: string; documentPath?: string }> {
    let after = '';
    for (;;) {
      const page = await inUnit(undefined, { write: false }, async (k) => {
        assertOrg(k, orgId);
        const p = new Params();
        const org = p.add(k.orgId);
        const { tagged, under } = connectorCondition(p, scope);
        const { rows } = await k.query<Row>(
          fill(
            `/* pipeshub:content.document_tagged_outside */ SELECT id, document_path FROM content.documents
            WHERE org_id = @{A} AND @{B} AND NOT COALESCE(@{C}, false) AND id > @{D} ORDER BY id LIMIT 500`,
            { A: org, B: tagged, C: under, D: p.add(after) },
          ),
          p.values,
        );
        return rows;
      });
      for (const r of page)
        yield {
          _id: r.id,
          ...(r.document_path === null
            ? {}
            : { documentPath: r.document_path }),
        };
      if (page.length < 500) return;
      after = page[page.length - 1]?.id ?? after;
    }
  }

  async deleteByConnector(
    orgId: string,
    scope: ConnectorScope,
  ): Promise<number> {
    return inUnit(undefined, { write: true }, async (k) => {
      assertOrg(k, orgId);
      const p = new Params();
      const org = p.add(k.orgId);
      const { tagged, under } = connectorCondition(p, scope);
      const r = await k.query(
        fill(
          `/* pipeshub:content.document_delete_by_connector */ DELETE FROM content.documents WHERE org_id = @{A} AND (@{B} OR @{C})`,
          { A: org, B: tagged, C: under },
        ),
        p.values,
      );
      return r.rowCount ?? 0;
    });
  }
}
