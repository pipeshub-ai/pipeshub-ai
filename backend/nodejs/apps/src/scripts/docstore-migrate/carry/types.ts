/**
 * Carriers (R45-CARRY, ADR-DOCMIG-01 amendment): the copy of data whose PostgreSQL form is not one row per document in the
 * generic mapping's sense. A carrier owns one store group as a unit (copy, follow with deletes, verify, rollback, reset) and
 * says which held collections and held fields it carries; a held collection a carrier carries is no longer held.
 *
 * A carrier sees documents as `SourceDoc`s (the `_id` apart, the rest as plain values: `plain.ts`), writes its rows inside a
 * unit of the import login, and compares by keys of its own: what PostgreSQL must hold for each document (`expected`) against
 * what it holds (`actual`), as canonical text, so a field it drops is a difference and never a silent loss.
 */
import type { Tx as KernelTx } from '../../../libs/db';

export type Doc = Record<string, unknown>;

/** A source document: its `_id` as text (an ObjectId as lower-case hex, `oid` set) and the rest of it as plain values. */
export interface SourceDoc {
  id: string;
  oid: boolean;
  doc: Doc;
  /** Set when the document cannot be carried as it is (a BSON value the store does not keep): why, with the path. */
  refused?: string;
}

/** The MongoDB side a carrier reads and, at a rollback, writes. A fake implements it in the unit tests. */
export interface CarryMongo {
  /** Every document of the collection, read as `sourceDoc` reads it (with `pick`: only those fields and `_id`). */
  scan(collection: string, read?: SourceRead): AsyncIterable<SourceDoc>;
  /** Writes whole documents by `_id` (insert or replace). */
  replace(collection: string, docs: SourceDoc[]): Promise<void>;
  /** Sets and removes fields of the documents named. */
  update(
    collection: string,
    ops: Array<{ id: string; oid: boolean; set: Doc; unset: string[] }>,
  ): Promise<void>;
  /** Removes the documents named; returns how many there were. */
  remove(
    collection: string,
    ids: Array<{ id: string; oid: boolean }>,
  ): Promise<number>;
}

/** How a collection is read: only `pick` paths, and `idPaths` that may hold an ObjectId (read as lower-case hex). */
export interface SourceRead {
  pick?: readonly string[];
  idPaths?: readonly string[];
  /** `id`: in `_id` order (a table numbers its rows in the order they are inserted). */
  order?: 'id';
}

export interface Restored {
  /** Documents written (MongoDB did not hold them as PostgreSQL gives them). */
  written: number;
  /** Documents MongoDB already held as PostgreSQL gives them (not written again). */
  unchanged?: number;
  removed: number;
}

export interface Carrier {
  readonly name: string;
  /** What it carries, for reports and the preflight list. */
  readonly reason: string;
  /** Collections it carries whole (held without it). */
  readonly collections: readonly string[];
  /** Fields of mapped collections it carries (held without it): collection -> document paths. */
  readonly fields: Readonly<Record<string, readonly string[]>>;
  /** How it reads a collection: only `pick` paths (a carrier of fields), and `idPaths` that may hold an ObjectId (read as hex). */
  readonly reads?: Readonly<Record<string, SourceRead>>;
  /** Tables its rows live in. */
  readonly tables: readonly string[];
  /** Writes the rows of these documents (insert or replace); rows a document no longer yields are removed. */
  write(k: KernelTx, collection: string, docs: SourceDoc[]): Promise<void>;
  /** The documents with these ids are gone from MongoDB: their rows go too (an erasure during the follow must not survive). */
  remove(k: KernelTx, collection: string, ids: string[]): Promise<void>;
  /** What PostgreSQL must hold for one document, by key (none: nothing); `note` counts a listed transform it applied. */
  expected(
    collection: string,
    doc: SourceDoc,
    note?: (transform: string) => void,
  ): Array<[string, unknown]>;
  /** What PostgreSQL holds for the collection, by the same keys. */
  actual(k: KernelTx, collection: string): Promise<Map<string, unknown>>;
  /** Rollback: makes the collection in MongoDB what PostgreSQL holds (documents and fields gone there are removed). */
  restore(
    k: KernelTx,
    collection: string,
    mongo: CarryMongo,
  ): Promise<Restored>;
  /**
   * A rule over the whole store group that a document-by-document compare cannot see (the audit chain re-checked on both sides):
   * the problems found, none when it holds.
   */
  check?(k: KernelTx, mongo: CarryMongo): Promise<string[]>;
  /** Removes everything it wrote (reset, and a resync before it copies again). */
  clear(k: KernelTx): Promise<void>;
}
