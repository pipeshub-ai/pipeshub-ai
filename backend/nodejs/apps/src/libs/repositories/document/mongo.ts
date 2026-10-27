import mongoose from 'mongoose';
import { DocumentModel } from '../../../modules/storage/schema/document.schema';
import type { StorageVendor } from '../../../modules/storage/types/storage.service.types';
import { ConflictError } from '../errors';
import { mapMongoError } from '../mongo';
import { toPlain } from '../plain';
import {
  ENVELOPE_PREFIXES,
  type ArtifactFence,
  type ConnectorScope,
  type DocumentPatch,
  type DocumentRepository,
  type DocumentRow,
  type DocumentTreeRow,
  type IdempotencyClaim,
  type NewDocument,
  type UpdateOptions,
} from './types';

const row = (doc: unknown): DocumentRow =>
  toPlain<DocumentRow>(doc, { json: true });

// Ids are passed as strings: Mongoose casts them to the stored type.

const MONGO_DUPLICATE_KEY = 11000;

// A virtual record's envelope documents wherever they are filed, plus anything
// at the flat path older records used, and (with `withRevisions`) every content
// revision stored for it. orgId sits in each branch so each one can use its own
// index.
function virtualRecordFilter(
  orgId: string,
  virtualRecordId: string,
  flatPath: string,
  withRevisions: boolean,
): Record<string, unknown> {
  return {
    $or: [
      { orgId: orgId, documentPath: flatPath },
      {
        orgId: orgId,
        documentName: {
          $in: ENVELOPE_PREFIXES.map((p) => `${p}${virtualRecordId}`),
        },
      },
      ...(withRevisions
        ? [{ orgId: orgId, 'artifactSet.virtualRecordId': virtualRecordId }]
        : []),
    ],
  };
}

function connectorFilters(scope: ConnectorScope): {
  connectorTag: Record<string, unknown>;
  underPrefixes: Record<string, unknown>[];
} {
  const connectorTag = {
    customMetadata: {
      $elemMatch: { key: 'connectorId', value: scope.connectorId },
    },
  };
  const underPrefixes: Record<string, unknown>[] = scope.prefixes.flatMap(
    (prefix) => [
      { documentPath: prefix },
      // '/' is followed by '0' in ASCII: every path under "prefix/", and no sibling.
      { documentPath: { $gte: `${prefix}/`, $lt: `${prefix}0` } },
    ],
  );
  return { connectorTag, underPrefixes };
}

/**
 * Moves the revision's write stamp and returns it, or null when there is no such revision.
 * A claimed revision is refused; a claim past the stale bound is from a delete that died and is
 * dropped here: nothing else would clear it once a record is back on the revision.
 */
async function touchArtifactSet(
  filter: Record<string, unknown>,
  { now, staleMs }: ArtifactFence,
): Promise<DocumentRow | null> {
  const writable = {
    ...filter,
    $or: [
      { 'artifactSet.retiringAt': { $exists: false } },
      { 'artifactSet.retiringAt': { $lt: now - staleMs } },
    ],
  };
  const touched = await DocumentModel.findOneAndUpdate(
    writable as never,
    { $set: { updatedAt: now }, $unset: { 'artifactSet.retiringAt': 1 } },
    { new: true, timestamps: false },
  );
  if (touched) return row(touched);
  if (await DocumentModel.findOne(filter as never)) {
    throw new ConflictError(
      'This revision is being deleted; write it again once the delete has finished',
    );
  }
  return null;
}

export class MongoDocumentRepository implements DocumentRepository {
  async get(orgId: string, id: string): Promise<DocumentRow | null> {
    const doc = await DocumentModel.findOne({ _id: id, orgId: orgId });
    return doc ? row(doc) : null;
  }

  async getLive(
    orgId: string,
    id: string,
    lean = false,
  ): Promise<DocumentRow | null> {
    const query = DocumentModel.findOne({
      _id: id,
      orgId: orgId,
      isDeleted: false,
    });
    const found = lean ? await query.lean().exec() : await query;
    return found ? toPlain<DocumentRow>(found, { json: true }) : null;
  }

  async create(input: NewDocument): Promise<DocumentRow> {
    try {
      return row(await DocumentModel.create(input as never));
    } catch (error) {
      throw mapMongoError(error);
    }
  }

  async createIdempotent(
    input: NewDocument,
    idempotency: IdempotencyClaim,
    firstAttemptFields: NewDocument,
  ): Promise<{ document: DocumentRow; replayed: boolean }> {
    const scope = {
      orgId: input.orgId,
      initiatorUserId: input.initiatorUserId ?? null,
      // $type repeats the index's partial filter: without it the planner cannot
      // tell the query stays inside that index, and scans the org instead.
      idempotencyKey: { $eq: idempotency.key, $type: 'string' },
    };
    const existing = await DocumentModel.findOne(scope as never);
    if (existing) {
      return { document: row(existing), replayed: true };
    }
    try {
      const keyed: NewDocument = {
        ...input,
        ...firstAttemptFields,
        idempotencyKey: idempotency.key,
        idempotencyFingerprint: idempotency.fingerprint,
      };
      const created = await DocumentModel.create(keyed as never);
      return { document: row(created), replayed: false };
    } catch (error) {
      if ((error as { code?: number }).code !== 11000) {
        throw mapMongoError(error);
      }
      const winner = await DocumentModel.findOne(scope as never);
      if (!winner) {
        throw mapMongoError(error);
      }
      return { document: row(winner), replayed: true };
    }
  }

  async update(
    orgId: string,
    id: string,
    patch: DocumentPatch,
    opts: UpdateOptions = {},
  ): Promise<DocumentRow | null> {
    // Load, assign, save: Mongoose writes only what changed, and a version
    // push stays a push, as when the caller held the loaded document.
    const doc = (await DocumentModel.findOne({
      _id: id,
      orgId: orgId,
    })) as unknown as
      | (Record<string, unknown> & {
          save: () => Promise<unknown>;
          versionHistory?: unknown[];
        })
      | null;
    if (!doc) {
      return null;
    }
    for (const [key, value] of Object.entries(patch)) {
      doc[key] = value;
    }
    if (opts.pushVersions?.length) {
      doc.versionHistory = doc.versionHistory ?? [];
      for (const version of opts.pushVersions) {
        doc.versionHistory.push(version);
      }
    }
    if (opts.holdLease !== undefined) {
      // Applies only while this attempt still holds its lease; clears it in the same write.
      doc.$where = { uploadLeaseToken: opts.holdLease };
      doc.uploadLeaseToken = undefined;
      doc.uploadLeaseExpiresAt = undefined;
      // Without a lease on the loaded row there is no change to write, and the guard would never run.
      // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- plain objects stand in for documents in unit tests
      (doc as unknown as mongoose.Document).markModified?.('uploadLeaseToken');
    }
    try {
      await doc.save();
    } catch (error) {
      if (error instanceof mongoose.Error.DocumentNotFoundError) {
        return null;
      }
      throw mapMongoError(error);
    }
    return row(doc);
  }

  async remove(id: string, orgId?: string): Promise<void> {
    await DocumentModel.deleteOne(
      orgId === undefined ? { _id: id } : { _id: id, orgId: orgId },
    );
  }

  async removeUnstored(id: string, vendor: StorageVendor): Promise<void> {
    await DocumentModel.deleteOne({
      _id: id,
      [vendor]: { $exists: false },
    });
  }

  async removeAwaitingDirectUpload(
    orgId: string,
    id: string,
  ): Promise<boolean> {
    const removed = await DocumentModel.findOneAndDelete({
      _id: id,
      orgId: orgId,
      awaitingDirectUpload: true,
    });
    return Boolean(removed);
  }

  async clearAwaitingDirectUpload(orgId: string, id: string): Promise<void> {
    await DocumentModel.updateOne(
      { _id: id, orgId: orgId },
      { $unset: { awaitingDirectUpload: '' } },
    );
  }

  async claimUpload(
    id: string,
    vendor: StorageVendor,
    leaseToken: string,
    leaseMs: number,
  ): Promise<DocumentRow | null> {
    const now = Date.now();
    const claimed = await DocumentModel.findOneAndUpdate(
      {
        _id: id,
        [vendor]: { $exists: false },
        $or: [
          { uploadLeaseExpiresAt: { $exists: false } },
          { uploadLeaseExpiresAt: { $lte: now } },
        ],
      },
      {
        $set: {
          uploadLeaseToken: leaseToken,
          uploadLeaseExpiresAt: now + leaseMs,
        },
      },
      { returnDocument: 'after' },
    );
    return claimed ? row(claimed) : null;
  }

  async releaseUpload(id: string, leaseToken: string): Promise<void> {
    await DocumentModel.updateOne(
      { _id: id, uploadLeaseToken: leaseToken },
      { $unset: { uploadLeaseToken: '', uploadLeaseExpiresAt: '' } },
    );
  }

  async ensureIndexes(): Promise<void> {
    // Indexes build in the background after connect, and the replay is only
    // sound once the unique index exists. Mongoose caches this promise.
    await DocumentModel.init();
  }

  async listByVirtualRecord(
    orgId: string,
    virtualRecordId: string,
    flatPath: string,
  ): Promise<DocumentRow[]> {
    const docs = await DocumentModel.find(
      virtualRecordFilter(orgId, virtualRecordId, flatPath, true),
    );
    return docs.map(row);
  }

  async listForRelocate(
    orgId: string,
    virtualRecordId: string,
    flatPath: string,
  ): Promise<DocumentTreeRow[]> {
    const docs = await DocumentModel.find({
      ...virtualRecordFilter(orgId, virtualRecordId, flatPath, false),
      'artifactSet.virtualRecordId': { $exists: false },
    })
      .select(
        '_id documentPath documentName extension isVersionedFile versionHistory customMetadata',
      )
      .lean<DocumentTreeRow[]>();
    return docs.map((d) => toPlain<DocumentTreeRow>(d));
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
    // createdAt is a Number of ms, not a Date: in BSON order every Number sorts before every
    // Date, so a plain $$NOW comparison would match every document.
    const olderThanSeconds = opts?.olderThanSeconds;
    const ageFilter =
      olderThanSeconds !== undefined &&
      Number.isFinite(olderThanSeconds) &&
      olderThanSeconds >= 0
        ? {
            $expr: {
              $and: [
                { $isNumber: '$createdAt' },
                {
                  $lt: [
                    '$createdAt',
                    {
                      $subtract: [
                        { $toLong: '$$NOW' },
                        olderThanSeconds * 1000,
                      ],
                    },
                  ],
                },
              ],
            },
          }
        : {};
    const docs = await DocumentModel.find({
      orgId: orgId,
      documentName: {
        $in: ENVELOPE_PREFIXES.map((p) => `${p}${virtualRecordId}`),
      },
      isDeleted: { $ne: true },
      ...ageFilter,
    })
      .select('_id documentName documentPath createdAt retiredAt')
      .sort({ createdAt: -1, _id: -1 })
      .lean<
        {
          _id: unknown;
          documentName?: string;
          documentPath?: string;
          createdAt?: number;
          retiredAt?: number | null;
        }[]
      >();
    return docs.map((d) => toPlain(d));
  }

  async retire(
    orgId: string,
    id: string,
    now: number,
  ): Promise<{ retiredAt: number | null } | null> {
    const marked = await DocumentModel.findOneAndUpdate(
      { _id: id, orgId: orgId, isDeleted: false, retiredAt: null },
      { $set: { retiredAt: now } },
      { new: true, timestamps: false },
    ).lean();
    if (marked) return { retiredAt: marked.retiredAt ?? null };
    const existing = await DocumentModel.findOne({
      _id: id,
      orgId: orgId,
      isDeleted: false,
    }).lean();
    return existing ? { retiredAt: existing.retiredAt ?? null } : null;
  }

  async pageFlatEnvelopes(
    orgId: string,
    flatPrefix: string,
    after: string | undefined,
    limit: number,
  ): Promise<{ _id: string; documentName?: string; documentPath?: string }[]> {
    // documentPath == "<prefix><vrid>" where vrid is the name after "record_"/"metadata_".
    const flatPathOf = (nameOffset: number): Record<string, unknown> => ({
      $eq: [
        '$documentPath',
        {
          $concat: [
            flatPrefix,
            { $substrCP: ['$documentName', nameOffset, 200] },
          ],
        },
      ],
    });
    const docs = await DocumentModel.find({
      orgId: orgId,
      ...(after ? { _id: { $gt: after } } : {}),
      isDeleted: { $ne: true },
      $or: ENVELOPE_PREFIXES.map((p) => ({
        documentName: new RegExp(`^${p}`),
        $expr: flatPathOf(p.length),
      })),
    })
      .select('_id documentName documentPath')
      .sort({ _id: 1 })
      .limit(limit)
      .lean<{ _id: unknown; documentName?: string; documentPath?: string }[]>();
    return docs.map((d) =>
      toPlain<{ _id: string; documentName?: string; documentPath?: string }>(d),
    );
  }

  async listByFolders(
    orgId: string,
    folderPaths: string[],
  ): Promise<DocumentRow[]> {
    const docs = await DocumentModel.find({
      orgId: orgId,
      documentPath: { $in: folderPaths },
    });
    return docs.map(row);
  }

  async findExistingIds(orgId: string, ids: string[]): Promise<string[]> {
    const rows = await DocumentModel.find({
      _id: { $in: ids },
      orgId: orgId,
      isDeleted: false,
    })
      .select('_id')
      .lean<{ _id: unknown }[]>()
      .exec();
    return rows.map((r) => String(r._id));
  }

  async subtree(orgId: string, path: string): Promise<DocumentTreeRow[]> {
    // '/' (0x2F) is immediately followed by '0' (0x30) in ASCII: this range
    // covers every documentPath that starts with "path/" without also matching
    // an unrelated sibling like "path2/...".
    const docs = await DocumentModel.find({
      orgId: orgId,
      $or: [
        { documentPath: path },
        { documentPath: { $gte: `${path}/`, $lt: `${path}0` } },
      ],
    })
      .select(
        '_id documentPath documentName extension isVersionedFile versionHistory isDeleted',
      )
      .lean<DocumentTreeRow[]>();
    return docs.map((d) => toPlain<DocumentTreeRow>(d));
  }

  async rewriteTree(
    oldPath: string,
    newPath: string,
    rows: { id: string; set: Record<string, unknown> }[],
  ): Promise<void> {
    // One bulkWrite: the documentPath rewrite (updateMany) and each row's own
    // field changes (updateOne) in one round-trip, which shrinks the window
    // between the disk rename and this update to a single operation.
    const bulkOps: Parameters<typeof DocumentModel.bulkWrite>[0] = [
      {
        updateMany: {
          filter: { _id: { $in: rows.map((r) => r.id) } },
          update: [
            {
              $set: {
                documentPath: {
                  $concat: [
                    newPath,
                    {
                      $substrCP: [
                        '$documentPath',
                        oldPath.length,
                        {
                          $subtract: [
                            { $strLenCP: '$documentPath' },
                            oldPath.length,
                          ],
                        },
                      ],
                    },
                  ],
                },
              },
            },
          ],
        },
      },
    ];
    for (const r of rows) {
      if (Object.keys(r.set).length > 0) {
        bulkOps.push({
          updateOne: { filter: { _id: r.id }, update: { $set: r.set } },
        });
      }
    }
    await DocumentModel.bulkWrite(bulkOps);
  }

  async setFields(id: string, set: Record<string, unknown>): Promise<void> {
    await DocumentModel.updateOne({ _id: id }, { $set: set });
  }

  async setFieldsMany(
    rows: { id: string; set: Record<string, unknown> }[],
  ): Promise<void> {
    if (rows.length === 0) return;
    await DocumentModel.bulkWrite(
      rows.map((r) => ({
        updateOne: { filter: { _id: r.id }, update: { $set: r.set } },
      })),
      { ordered: false },
    );
  }

  async createArtifactSetOnce(
    input: NewDocument,
    fence: ArtifactFence,
  ): Promise<DocumentRow> {
    const filter = {
      orgId: input.orgId,
      'artifactSet.virtualRecordId': {
        $eq: input.artifactSet?.virtualRecordId,
      },
      'artifactSet.rev': { $eq: input.artifactSet?.rev },
    };
    const existing = await touchArtifactSet(filter, fence);
    if (existing) return existing;
    try {
      return row(await DocumentModel.create(input as never));
    } catch (error) {
      // Another presign of the same revision created it first.
      if ((error as { code?: unknown } | null)?.code !== MONGO_DUPLICATE_KEY) {
        throw mapMongoError(error);
      }
      const winner = await touchArtifactSet(filter, fence);
      if (!winner) throw mapMongoError(error);
      return winner;
    }
  }

  async claimArtifactSetForDelete(
    orgId: string,
    id: string,
    {
      now,
      staleMs,
      ifUnchangedSince,
    }: {
      now: number;
      staleMs: number;
      ifUnchangedSince?: number;
    },
  ): Promise<DocumentRow | 'changed' | null> {
    const filter = {
      _id: { $eq: id },
      orgId: orgId,
      artifactSet: { $exists: true },
    };
    // Without a stamp the delete is unfenced: it claims whatever is there.
    const guarded =
      ifUnchangedSince === undefined
        ? filter
        : {
            ...filter,
            updatedAt: { $lte: ifUnchangedSince },
            $or: [
              { 'artifactSet.retiringAt': { $exists: false } },
              { 'artifactSet.retiringAt': { $lt: now - staleMs } },
            ],
          };
    // timestamps off: the claim is not a write of the revision, and a delete retried after a
    // crash must still match the listed stamp.
    const claimed = await DocumentModel.findOneAndUpdate(
      guarded as never,
      { $set: { 'artifactSet.retiringAt': now } },
      { new: true, timestamps: false },
    );
    if (claimed) return row(claimed);
    const present =
      ifUnchangedSince !== undefined &&
      (await DocumentModel.findOne(filter as never)) !== null;
    return present ? 'changed' : null;
  }

  async renewArtifactSetClaim(
    orgId: string,
    id: string,
    known: number[],
    next: number,
  ): Promise<void> {
    const claimed = {
      _id: id,
      orgId: orgId,
      'artifactSet.retiringAt': { $in: known },
    };
    await DocumentModel.updateOne(
      claimed as never,
      { $set: { 'artifactSet.retiringAt': next } },
      { timestamps: false },
    );
  }

  async removeClaimedArtifactSet(
    id: string,
    orgId: string,
    claims: number[],
  ): Promise<void> {
    const claimed = {
      'artifactSet.retiringAt': { $in: claims },
      _id: id,
      orgId: orgId,
    };
    await DocumentModel.deleteOne(claimed as never);
  }

  async listArtifactSets(
    orgId: string,
    virtualRecordId: string,
  ): Promise<DocumentRow[]> {
    const docs = await DocumentModel.find({
      orgId: orgId,
      'artifactSet.virtualRecordId': { $eq: virtualRecordId },
      isDeleted: { $ne: true },
    })
      .sort({ createdAt: 1, _id: 1 })
      .select('_id artifactSet storageVendor createdAt updatedAt')
      .lean();
    return docs.map(row);
  }

  async pageByConnector(
    orgId: string,
    scope: ConnectorScope,
    after: string | undefined,
    limit: number,
  ): Promise<{ _id: string; documentName?: string; documentPath?: string }[]> {
    const { connectorTag, underPrefixes } = connectorFilters(scope);
    const filter: Record<string, unknown> = {
      orgId: orgId,
      $or: [connectorTag, ...underPrefixes],
    };
    if (after !== undefined) {
      filter._id = { $gt: after };
    }
    const docs = await DocumentModel.find(filter)
      .sort({ _id: 1 })
      .limit(limit)
      .select('_id documentName documentPath')
      .lean<{ _id: unknown; documentName?: string; documentPath?: string }[]>();
    return docs.map((d) =>
      toPlain<{ _id: string; documentName?: string; documentPath?: string }>(d),
    );
  }

  async *streamTaggedOutsidePrefixes(
    orgId: string,
    scope: ConnectorScope,
  ): AsyncIterable<{ _id: string; documentPath?: string }> {
    const { connectorTag, underPrefixes } = connectorFilters(scope);
    const cursor = DocumentModel.find({
      orgId: orgId,
      ...connectorTag,
      $nor: underPrefixes,
    })
      .select('_id documentPath')
      .lean<{ _id: unknown; documentPath?: string }[]>()
      .cursor();
    for await (const doc of cursor) {
      yield toPlain<{ _id: string; documentPath?: string }>(doc);
    }
  }

  async deleteByConnector(
    orgId: string,
    scope: ConnectorScope,
  ): Promise<number> {
    const { connectorTag, underPrefixes } = connectorFilters(scope);
    const result = await DocumentModel.deleteMany({
      orgId: orgId,
      $or: [connectorTag, ...underPrefixes],
    });
    return result.deletedCount;
  }
}
