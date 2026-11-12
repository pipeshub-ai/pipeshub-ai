import { Logger } from '../../../../libs/services/logger.service';
import {
  ARTIFACT_SET_INDEX,
  ARTIFACT_SET_REVISION_INDEX_KEY,
  DocumentModel,
} from '../../../storage/schema/document.schema';

const MONGO_NAMESPACE_NOT_FOUND = 26;
const MONGO_INDEX_NOT_FOUND = 27;

export type ArtifactSetIndexStatus =
  'already_done' | 'replaced' | 'no_collection' | 'failed';

export interface ArtifactSetIndexResult {
  status: ArtifactSetIndexStatus;
}

const sameKey = (
  index: { key: Record<string, unknown> },
  key: Record<string, unknown>,
): boolean => JSON.stringify(index.key) === JSON.stringify(key);

/**
 * Moves the artifact sets' unique index from one document per revision to one per stored write.
 *
 * Every stored write of a revision now files its set in a document of its own (`artifactSet.writeId`),
 * so a write that lost its place, or an upload storage commits late, can never land in another write's
 * set. The index on (orgId, virtualRecordId, rev) refuses that second document as a duplicate key, and
 * Mongoose's boot-time `autoIndex` only adds indexes, never drops one. This drops it.
 *
 * The index on (orgId, virtualRecordId, rev, writeId) is built first (the schema builds it at boot too;
 * whichever runs first wins and the other is a no-op), then the old one goes, so the collection is never
 * without a uniqueness rule. Every existing document already satisfies the new index: it is looser than
 * the old one. Until this has run, a second write's set of a revision is refused and that write keeps
 * no set; its reads are served from the stored record.
 *
 * Runs every boot. After the first time it reads the index list and stops.
 */
export class ArtifactSetIndexMigration {
  constructor(private readonly logger: Logger) {}

  async run(): Promise<ArtifactSetIndexResult> {
    try {
      let indexes: Awaited<ReturnType<typeof DocumentModel.collection.indexes>>;
      try {
        indexes = await DocumentModel.collection.indexes();
      } catch (error) {
        if (
          (error as { code?: unknown } | null)?.code ===
          MONGO_NAMESPACE_NOT_FOUND
        ) {
          // A fresh database: the schema builds the new index with the collection.
          return { status: 'no_collection' };
        }
        throw error;
      }
      const old = indexes.find(
        (i) => i.unique === true && sameKey(i, ARTIFACT_SET_REVISION_INDEX_KEY),
      );
      if (old?.name === undefined) {
        return { status: 'already_done' };
      }
      if (!indexes.some((i) => sameKey(i, ARTIFACT_SET_INDEX.keys))) {
        await DocumentModel.collection.createIndex(
          ARTIFACT_SET_INDEX.keys,
          ARTIFACT_SET_INDEX.options,
        );
      }
      try {
        await DocumentModel.collection.dropIndex(old.name);
      } catch (error) {
        // Another process dropped it first.
        if (
          (error as { code?: unknown } | null)?.code !== MONGO_INDEX_NOT_FOUND
        ) {
          throw error;
        }
      }
      this.logger.info(
        'Artifact sets: one document per stored write; dropped the index of one per revision',
        { dropped: old.name },
      );
      return { status: 'replaced' };
    } catch (error) {
      this.logger.error(
        'Artifact set index migration failed; a second stored write of a revision keeps no artifact set until it succeeds',
        { error: error instanceof Error ? error.message : 'Unknown error' },
      );
      return { status: 'failed' };
    }
  }
}
