import type { SourceStatus } from './record-access';

export interface ArtifactReadQuery {
  /** The viewer's own bearer credential; the authority resolves whose access is checked from it. */
  authorization: string;
  artifactIds: readonly string[];
}

/**
 * Which artifacts of a conversation a reader who does not own it may see now
 * (SS0). `readable` only when the artifact's provenance is known and every
 * source in it is readable by the viewer; `unresolved` when its provenance is
 * unknown. Rejects with `RecordAccessUnavailableError` when it cannot decide,
 * so callers fail closed.
 */
export interface ArtifactReadAccess {
  decide(query: ArtifactReadQuery): Promise<ReadonlyMap<string, SourceStatus>>;
}
