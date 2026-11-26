/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous port without I/O. */
import {
  ArtifactReadAccess,
  ArtifactReadQuery,
} from '../ports/artifact-access';
import {
  RecordAccessFailure,
  RecordAccessUnavailableError,
  SourceStatus,
} from '../ports/record-access';

/**
 * Readable artifacts per credential; every other artifact is `unresolved`
 * (provenance unknown), as the production authority answers today.
 * `queries` records every call, `failWith` makes the next ones reject,
 * `dropAnswers` answers for nothing.
 */
export class InMemoryArtifactReadAccess implements ArtifactReadAccess {
  readonly queries: ArtifactReadQuery[] = [];
  failWith: RecordAccessFailure | null = null;
  dropAnswers = false;
  private readonly readable = new Map<string, Set<string>>();
  private readonly denied = new Set<string>();

  grant(authorization: string, ...artifactIds: string[]): this {
    const granted = this.readable.get(authorization) ?? new Set<string>();
    for (const id of artifactIds) granted.add(id);
    this.readable.set(authorization, granted);
    return this;
  }

  /** Provenance known, and some source in it unreadable for everyone. */
  deny(...artifactIds: string[]): this {
    for (const id of artifactIds) this.denied.add(id);
    return this;
  }

  async decide(
    query: ArtifactReadQuery,
  ): Promise<ReadonlyMap<string, SourceStatus>> {
    this.queries.push({
      authorization: query.authorization,
      artifactIds: [...query.artifactIds],
    });
    if (this.failWith !== null) {
      throw new RecordAccessUnavailableError(this.failWith, 'injected failure');
    }
    const decided = new Map<string, SourceStatus>();
    if (this.dropAnswers) return decided;
    const granted = this.readable.get(query.authorization) ?? new Set<string>();
    for (const id of query.artifactIds) {
      decided.set(
        id,
        granted.has(id)
          ? 'readable'
          : this.denied.has(id)
            ? 'denied'
            : 'unresolved',
      );
    }
    return decided;
  }
}
