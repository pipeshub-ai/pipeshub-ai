/**
 * One session's documents out of a database and into another (P9): an archive
 * directory of Extended JSON (one file per collection, types kept: ObjectId,
 * Date, Binary) and a manifest with counts and digests. The selectors mirror
 * what erasure reaches (`MongoSessionStoreEraser`, `HarnessConversationEraser`),
 * so a backup holds exactly what an erasure would remove, plus the org's
 * wrapped data keys that unseal it.
 */
import { chmod, mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { BSON, Db, Document, Filter, ObjectId } from 'mongodb';

const { EJSON } = BSON;
import { sessionWorkspaceId } from '../../../../modules/agent-harness/domain/workspace';
import {
  archiveFileDigest,
  manifestDigest,
  SESSION_BACKUP_COVERAGE,
  SESSION_BACKUP_FORMAT,
  SessionBackupManifest,
  SessionBackupRefused,
} from '../../../../modules/agent-harness/domain/session-backup';

interface Selection {
  orgId: string;
  sessionId: string;
  session: ObjectId | null;
  org: ObjectId | null;
  runIds: string[];
  streamIds: string[];
  citationIds: ObjectId[];
}

const escapeRegExp = (text: string): string =>
  text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const idsOf = (selection: Selection): Array<string | ObjectId> =>
  selection.session === null
    ? [selection.sessionId]
    : [selection.sessionId, selection.session];

const orgIdsOf = (selection: Selection): Array<string | ObjectId> =>
  selection.org === null ? [selection.orgId] : [selection.orgId, selection.org];

/** A session's own document in a legacy chat collection, under its id as a string or as an ObjectId. */
const sessionDocuments = (selection: Selection): Filter<Document> => {
  // The driver types `_id` as an ObjectId alone; these collections hold either form.
  const filter = {
    _id: { $in: idsOf(selection) },
    orgId: { $in: orgIdsOf(selection) },
  };
  return filter as Filter<Document>;
};

/** In order: later selectors read what earlier ones found (run ids, stream ids, citation ids). */
const SELECTORS: ReadonlyArray<[string, (s: Selection) => Filter<Document>]> = [
  ['agentRuns', (s) => ({ orgId: s.orgId, sessionId: s.sessionId })],
  ['sessionEvents', (s) => ({ orgId: s.orgId, sessionId: s.sessionId })],
  [
    'chatSessionMessages',
    (s) => ({
      sessionId: { $in: idsOf(s) },
      orgId: { $in: orgIdsOf(s) },
    }),
  ],
  ['chatSessions', sessionDocuments],
  [
    'citations',
    (s) => ({ _id: { $in: s.citationIds }, 'metadata.orgId': s.orgId }),
  ],
  ['conversations', sessionDocuments],
  ['agentconversations', sessionDocuments],
  ['agentNextRuns', (s) => ({ orgId: s.orgId, sessionId: s.sessionId })],
  ['agentEvents', (s) => ({ orgId: s.orgId, sessionId: s.sessionId })],
  [
    'agentCommandReceipts',
    (s) => ({ orgId: s.orgId, runId: { $in: s.runIds } }),
  ],
  [
    'agentAdmissionReceipts',
    (s) => ({ 'result.sessionId': s.sessionId, 'result.run.orgId': s.orgId }),
  ],
  [
    'agentSessionForks',
    (s) => ({
      orgId: s.orgId,
      $or: [{ sourceSessionId: s.sessionId }, { forkSessionId: s.sessionId }],
    }),
  ],
  [
    'agentJournalPayloads',
    (s) => ({ orgId: s.orgId, 'owner.sessionId': s.sessionId }),
  ],
  ...(
    [
      'agentWorkspaceManifests',
      'agentWorkspaceHeads',
      'agentWorkspaceWriters',
    ] as const
  ).map((name): [string, (s: Selection) => Filter<Document>] => [
    name,
    (s) => ({
      orgId: s.orgId,
      workspaceId: {
        $regex: `^${escapeRegExp(sessionWorkspaceId(s.sessionId))}(?::|$)`,
      },
    }),
  ]),
  ['agentWorkspaceKeys', (s) => ({ orgId: s.orgId, sessionId: s.sessionId })],
  ['agentEventCounters', (s) => ({ orgId: s.orgId, sessionId: s.sessionId })],
  [
    'sessionStreamCounters',
    (s) => ({ orgId: s.orgId, streamId: { $in: s.streamIds } }),
  ],
  ['agentHarnessDataKeys', (s) => ({ orgId: s.orgId })],
];

/** The stores backed up, each with how it is selected; equal to the coverage's copied stores (C-14). */
export const SESSION_BACKUP_SELECTED: readonly string[] = SELECTORS.map(
  ([name]) => name,
);

const objectIdOrNull = (value: string): ObjectId | null =>
  ObjectId.isValid(value) && /^[0-9a-f]{24}$/i.test(value)
    ? new ObjectId(value)
    : null;

const ORG_STORES = new Set(
  Object.entries(SESSION_BACKUP_COVERAGE)
    .filter(([, coverage]) => 'copy' in coverage && coverage.copy === 'org')
    .map(([name]) => name),
);

/** 0600 whether the file is new or overwritten (`mode` applies only when the file is created). */
const writePrivate = async (path: string, text: string): Promise<void> => {
  await writeFile(path, text, { mode: 0o600 });
  await chmod(path, 0o600);
};

export class MongoSessionBackup {
  constructor(private readonly db: Db) {}

  async export(
    orgId: string,
    sessionId: string,
    directory: string,
  ): Promise<SessionBackupManifest> {
    // The messages and citations are plaintext in the archive: only the operator who made it may read it.
    await mkdir(directory, { recursive: true, mode: 0o700 });
    await chmod(directory, 0o700);
    const selection: Selection = {
      orgId,
      sessionId,
      session: objectIdOrNull(sessionId),
      org: objectIdOrNull(orgId),
      runIds: [],
      streamIds: [],
      citationIds: [],
    };
    const collections: SessionBackupManifest['collections'] = {};
    for (const [name, select] of SELECTORS) {
      const documents = await this.db
        .collection(name)
        .find(select(selection))
        .toArray();
      this.learn(name, documents, selection);
      const text = documents
        .map((doc) => EJSON.stringify(doc, { relaxed: false }))
        .join('\n');
      await writePrivate(join(directory, `${name}.ndjson`), text);
      collections[name] = {
        documents: documents.length,
        sha256: archiveFileDigest(text),
      };
    }
    const manifest: SessionBackupManifest = {
      format: SESSION_BACKUP_FORMAT,
      orgId,
      sessionId,
      createdAt: new Date().toISOString(),
      collections,
      digest: manifestDigest(collections, archiveFileDigest),
    };
    await writePrivate(
      join(directory, 'manifest.json'),
      JSON.stringify(manifest, null, 2),
    );
    return manifest;
  }

  /**
   * Into a database that does not hold the session (`session_exists`
   * otherwise); the archive is verified whole before anything is written.
   */
  async restore(directory: string): Promise<SessionBackupManifest> {
    const manifest = JSON.parse(
      await readFile(join(directory, 'manifest.json'), 'utf-8'),
    ) as SessionBackupManifest;
    if (manifest.format !== SESSION_BACKUP_FORMAT) {
      throw new SessionBackupRefused(
        'archive_format',
        `not a ${SESSION_BACKUP_FORMAT} archive`,
      );
    }
    const contents = new Map<string, string>();
    for (const [name, entry] of Object.entries(manifest.collections)) {
      const text = await readFile(join(directory, `${name}.ndjson`), 'utf-8');
      if (archiveFileDigest(text) !== entry.sha256) {
        throw new SessionBackupRefused(
          'archive_corrupt',
          `${name} does not match the manifest`,
        );
      }
      contents.set(name, text);
    }
    if (
      manifestDigest(manifest.collections, archiveFileDigest) !==
      manifest.digest
    ) {
      throw new SessionBackupRefused(
        'archive_corrupt',
        'the manifest digest does not match',
      );
    }
    const present = await this.db
      .collection('agentRuns')
      .findOne({ orgId: manifest.orgId, sessionId: manifest.sessionId });
    const events = await this.db
      .collection('sessionEvents')
      .findOne({ orgId: manifest.orgId, sessionId: manifest.sessionId });
    if (present !== null || events !== null) {
      throw new SessionBackupRefused(
        'session_exists',
        'the target database already holds this session',
      );
    }
    for (const [name, text] of contents) {
      const documents =
        text === ''
          ? []
          : text
              .split('\n')
              .map((line) => EJSON.parse(line, { relaxed: false }) as Document);
      if (documents.length === 0) continue;
      if (ORG_STORES.has(name)) {
        // The org's keys may already be there (an earlier restore, the same org): never overwrite one.
        for (const document of documents) {
          // The driver types `_id` as an ObjectId; a document kept under a string id is matched by it the same.
          const id = document['_id'] as ObjectId;
          await this.db
            .collection(name)
            .updateOne(
              { _id: id },
              { $setOnInsert: document },
              { upsert: true },
            );
        }
        continue;
      }
      await this.db.collection(name).insertMany(documents, { ordered: true });
    }
    return manifest;
  }

  private learn(
    name: string,
    documents: Document[],
    selection: Selection,
  ): void {
    if (name === 'agentRuns')
      selection.runIds = documents.map((doc) => String(doc['runId']));
    if (name === 'sessionEvents') {
      selection.streamIds = [
        ...new Set(documents.map((doc) => String(doc['streamId']))),
      ];
    }
    if (name === 'chatSessionMessages') {
      selection.citationIds = [
        ...new Set(
          documents.flatMap((doc) =>
            (
              (doc['citations'] as
                Array<{ citationId?: unknown }> | undefined) ?? []
            ).flatMap((citation) =>
              // An ObjectId or a string: either prints as the id.
              citation.citationId
                ? [String(citation.citationId as { toString(): string })]
                : [],
            ),
          ),
        ),
      ].flatMap((id) => {
        const oid = objectIdOrNull(id);
        return oid === null ? [] : [oid];
      });
    }
  }
}
