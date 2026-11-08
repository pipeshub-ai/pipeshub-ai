import type { Tx } from '../uow';

/** A stored search hit, as the history and by-id reads return it. */
export interface CitationDto {
  _id: string;
  content: string;
  chunkIndex: number;
  citationType: string;
  metadata: Record<string, unknown>;
  createdAt: Date;
  updatedAt: Date;
  /** Present on a row read in full; the by-id read leaves it out. */
  __v?: number;
}

export interface SearchShareDto {
  /** The store gives each entry an id today. */
  _id?: string;
  userId: string;
  accessLevel: 'read' | 'write';
}

/**
 * A saved search. `citationIds` holds hit documents where the read populates
 * them and bare ids otherwise (a hit whose document is gone is dropped by the
 * populating read).
 */
export interface SemanticSearchDto {
  _id: string;
  query: string;
  limit: number;
  orgId: string;
  userId: string;
  citationIds: Array<string | CitationDto>;
  records: Record<string, string>;
  isShared: boolean;
  shareLink?: string;
  sharedWith: SearchShareDto[];
  isArchived: boolean;
  archivedBy?: string | null;
  createdAt: Date;
  updatedAt: Date;
  __v: number;
}

export interface NewSearchHit {
  content: string;
  chunkIndex: number;
  citationType: string;
  metadata: Record<string, unknown>;
}

export interface NewSemanticSearch {
  orgId: string;
  userId: string;
  query: string;
  limit: number;
  /** Saved in order; `undefined` stores the search without a hit list. */
  hits?: NewSearchHit[];
  /** Record id to its JSON text. */
  records: Map<string, string>;
}

/**
 * Which saved searches a viewer can see. Everything is already validated and
 * escaped by the caller (`search` is regex-escaped text, dates are real dates).
 */
export interface SearchQuery {
  /** Searches the viewer wrote. Default true. At least one of owned/shared must be true. */
  owned?: boolean;
  /** Searches shared with the viewer. Default true. */
  shared?: boolean;
  /** Escaped text matched case-insensitively. */
  search?: string;
  startDate?: Date;
  endDate?: Date;
  /** Only searches whose share flag equals this (the `?shared=` filter). */
  isShared?: boolean;
}

export interface SearchListQuery extends SearchQuery {
  /** Default `lastActivityAt`, descending; `_id` descending always breaks ties. */
  sort?: { field: 'createdAt' | 'lastActivityAt' | 'title'; direction: 1 | -1 };
  skip: number;
  limit: number;
}

export interface SearchSharing {
  isShared?: boolean;
  shareLink?: string;
  sharedWith: Array<{ userId: string; accessLevel: 'read' | 'write' }>;
}

/**
 * Methods that change a search return the row as it was BEFORE the change
 * (today's `findByIdAndUpdate` without `new`); the callers' responses rely on that.
 */
export interface SemanticSearchRepository {
  /** Saves the hits (one ordered insert), then the search pointing at them. */
  create(input: NewSemanticSearch, tx?: Tx): Promise<SemanticSearchDto>;

  /**
   * One non-archived search the viewer can see (own or shared with them), or,
   * with `archived: true`, one archived search. `populateCitations` reads the
   * hits as documents (the by-id read).
   */
  findVisible(
    orgId: string,
    viewerId: string,
    id: string,
    opts?: {
      archived?: boolean;
      populateCitations?: boolean;
      /** The request's own narrowing (`search`, dates, `shared`) applies to a read by id too. */
      query?: SearchQuery;
    },
  ): Promise<SemanticSearchDto | null>;

  /** One page of the viewer's visible searches and the count of all of them in the org. */
  listVisible(
    orgId: string,
    viewerId: string,
    q: SearchListQuery,
  ): Promise<{ rows: SemanticSearchDto[]; total: number }>;

  /** Replaces the sharing fields of a search (by id; the caller checked visibility). */
  setSharing(
    id: string,
    sharing: SearchSharing,
    tx?: Tx,
  ): Promise<SemanticSearchDto | null>;

  /**
   * Replaces the recipients of a search after some were removed. When none
   * remain the search stops being shared and loses its link; otherwise
   * the sharing flag and the link stay. Unlike the other changes it returns the
   * row AFTER the change, which is what the unshare response reports.
   */
  unshare(
    id: string,
    remaining: SearchSharing['sharedWith'],
    tx?: Tx,
  ): Promise<SemanticSearchDto | null>;

  /** Archives a search (by id; the caller checked visibility). */
  archive(
    id: string,
    byUserId: string,
    tx?: Tx,
  ): Promise<SemanticSearchDto | null>;

  /** Unarchives an archived search the viewer can see. */
  unarchive(
    orgId: string,
    viewerId: string,
    id: string,
    query?: SearchQuery,
    tx?: Tx,
  ): Promise<SemanticSearchDto | null>;

  /** Deletes one visible search and its hits; null when none matched. */
  delete(
    orgId: string,
    viewerId: string,
    id: string,
    query?: SearchQuery,
    tx?: Tx,
  ): Promise<SemanticSearchDto | null>;

  /** Deletes every visible search matching `q` and their hits; returns how many searches went. */
  deleteVisible(
    orgId: string,
    viewerId: string,
    q: SearchQuery,
    tx?: Tx,
  ): Promise<number>;

  /** Hit documents of the org by id (ids of other orgs or deleted hits are left out). */
  findCitations(orgId: string, ids: string[]): Promise<CitationDto[]>;
}
