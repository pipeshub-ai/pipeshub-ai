import type { ChangeEvent } from '../contracts/generated';
import type { Snapshot } from '../domain/resolution';

/**
 * What a pin reads, cached by head revision (03-interfaces §4.5, 02-lld §8.1).
 * A miss costs at most one batched read per store. `onChange` applies the
 * revision rule (`changeStep`): untouched entries advance, touched ones reload
 * on their next read, a gap drops the org's entries. The writing instance
 * applies its own event before answering the write (read-your-writes).
 */
export interface SnapshotSource {
  snapshot(
    orgId: string,
    agentKey: string | null,
    actorId: string,
  ): Promise<Snapshot>;
  onChange(event: ChangeEvent): void;
}
