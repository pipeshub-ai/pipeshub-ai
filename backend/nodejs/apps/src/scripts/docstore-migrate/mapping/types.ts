import type { Db } from 'mongodb';
import type { Tx as KernelTx } from '../../../libs/db';
import type { KeyEntry } from '../carry/keymap';

/**
 * A table that does not keep the document id (R45-CARRY): the key map records which row each document became, so the follower can
 * remove the row of a deleted document and a rollback can tell a row removed in PostgreSQL from one that was never copied.
 */
export interface KeyedRows {
  /** The row's key columns after `org_id`, in the order of the key map's row key. */
  columns: string[];
  /** The rows the (copied, not skipped) documents became, read in the unit that wrote them. */
  entries(
    k: KernelTx,
    docs: Array<Record<string, unknown>>,
  ): Promise<KeyEntry[]>;
}

/**
 * The verifier's own description of how a MongoDB collection is laid out in PostgreSQL. It is written and reviewed
 * separately from the repositories' mappers and imports nothing from them: a field a mapper drops then shows up as a
 * difference instead of disappearing from both sides.
 *
 * A document is rebuilt from its rows like this: the root row gives `_id` (from `idCol`), every `fields` entry puts its
 * column at its path (dots nest), a child table gives the array at its path in `orderCol` order. A NULL column leaves its
 * key out (`presence: 'omit'`), writes `null` (`'null'`), or writes `null` only when the path is listed in the row's
 * `nulls` array (`'listed'`).
 */
export type ColType =
  | 'oid' //          text id; compared as lower-case hex
  | 'text'
  | 'int'
  | 'float'
  | 'bool'
  | 'date' //         timestamptz; the document holds a Date
  | 'epoch_ms' //     timestamptz; the document holds a number of milliseconds
  | 'json' //         jsonb; any JSON value, key order ignored
  | 'bytea_b64' //    bytea; the document holds base64 text
  | 'bigint' //       bigint or numeric; a number when exact, else a decimal string
  | 'oid_array' //    array column of ids
  | 'text_array' //   array column of text
  | 'sha256'; //      the column holds the SHA-256 hex of the string at the path

export type Presence = 'omit' | 'null' | 'listed';

export interface FieldMap {
  col: string;
  /** Dotted path in the document. `''` in a child table means the array element itself. */
  path: string;
  type: ColType;
  /** Default `omit`. */
  presence?: Presence;
  /**
   * With `path: ''` on a root field: the column holds an object whose keys sit at the top of the document (the message's harness
   * fields). The keys listed are the document paths it may hold; the decoder spreads the object into the document.
   */
  spread?: string[];
  /** Value the document holds when the column does not (a constant the schema defaults); the decoder leaves it out when the column equals it. */
  absentWhen?: unknown;
  /** The schema default for a field a document never stored: the source side is read as holding it (listed transform, counted), because the column is NOT NULL. */
  readAbsentAs?: unknown;
}

export interface ChildMap {
  table: string;
  /** Column that holds the root row's id. */
  parentCol: string;
  /** Path of the array in the document. */
  path: string;
  /** Column that keeps the array position. */
  orderCol: string;
  /** Default true: no rows means `[]`, and a source document without the array is treated as holding `[]` (listed transform). False: no rows means the key is absent. */
  emptyIsArray?: boolean;
  fields: FieldMap[];
  /** Columns that are keys or copies of the parent's (org, parent id, entry ids): not part of the document. */
  derived: string[];
}

export interface CollectionMapping {
  /** MongoDB collection name. */
  collection: string;
  /** Directory name of the repository that owns the rows (see `libs/repositories/all.ts`). */
  repository: string;
  table: string;
  /** Column holding the document's `_id`. */
  idCol: string;
  /** SQL type of `idCol` when it is not text (the outbox keeps a bigint). */
  idType?: 'bigint';
  /** Document path of the organisation id (`_id` for the organisation itself); null when the collection is not per organisation. */
  orgPath: string | null;
  orgCol: string | null;
  /** Column holding Mongoose's `__v`. */
  versionCol?: string;
  /** Column listing the paths that were stored as `null` (`presence: 'listed'`). */
  nullsCol?: string;
  fields: FieldMap[];
  children?: ChildMap[];
  /** Root columns that are keys, copies or computed from other fields: not part of the document. */
  derived: string[];
  /** Document paths deliberately not copied, with the reason; the verifier removes them from the source side and lists them. */
  dropped?: Array<{ path: string; reason: string }>;
  /**
   * Document paths the generic copy does not write: a carrier copies them (`../carry`, `fieldCarrier`), or, while none does, they
   * are held and preflight is red while any document holds one (the move is refused, never lossy). Either way the generic verifier
   * removes them from the source side and counts them.
   */
  held?: Array<{ path: string; reason: string }>;
  /** The generic document-by-document comparison does not apply; `verify-identity.ts` compares this collection (credentials per address, slug counters). */
  custom?: boolean;
  /** The id a source document is known by in the table (default: its `_id`); the decoder's `_id` is this too. */
  sourceKey?: (doc: Record<string, unknown>) => string;
  /** The table key of a source `_id` (lower-case text) when the two differ: the outbox folds an ObjectId into a bigint. */
  tableId?: (sourceId: string) => string;
  /**
   * SQL that returns the rows breaking a rule that must hold after the copy (a citation stamped with another
   * organisation than its message, a member whose user is missing). Zero rows = holds. Run by the verifier with the jobs login
   * (or the migrate login for a global table); keep it read-only and bounded with LIMIT 20.
   */
  invariants?: Array<{ name: string; sql: string }>;
  /** MongoDB documents a rollback never removes (they have no row of this table: pre-organisation attempts live in another one). */
  keepOnReverse?: Record<string, unknown>;
  /** The table keeps no document id: rows are keyed through the key map. */
  keyed?: KeyedRows;
  /** Read the whole collection in one batch (a mapping whose importer must see every document at once). */
  oneBatch?: boolean;
  /** Source paths a listed transform consumes (so the path census does not report them as unknown). */
  censusKnown?: string[];
  /** Source paths where a stored null is accepted: a listed transform reads it as absent. */
  nullOk?: string[];
  /** Listed transforms the copy applies to the source side (`apply` changes the document in place and says whether it did). */
  transforms?: Array<{
    name: string;
    apply(doc: Record<string, unknown>): boolean;
  }>;
  /**
   * Called with a batch of raw source documents before `transforms` run on them, for a transform that needs other documents
   * (read from the source here, so one created after the batch it is looking for was read is still found). `db` is the application's
   * main database, where the chats are, even for a collection read from another (the Slack bot's `MONGO_SLACK_DB_NAME`).
   */
  beforeTransforms?: (db: Db, raws: unknown[]) => Promise<void>;
  /** Documents the copy leaves out, with the reason (null: copy it). */
  skip?: (doc: Record<string, unknown>) => string | null;
}

/** A collection that is deliberately not copied, with the reason. */
export interface IgnoredCollection {
  collection: string;
  reason: string;
  /** PostgreSQL tables of the same data that the copy therefore leaves empty. */
  tables?: string[];
}

/**
 * A collection whose data must survive a move and which the generic path does not copy: a carrier copies it (`CARRIED`), or,
 * while none does, it is held (`HELD`): preflight is red while it holds documents, so a move is refused instead of dropping it.
 */
export interface HeldCollection {
  collection: string;
  reason: string;
  /** The PostgreSQL tables its data lives in under `DOC_STORE=postgres`; none for an edition's collection kept in MongoDB only. */
  tables: string[];
}
