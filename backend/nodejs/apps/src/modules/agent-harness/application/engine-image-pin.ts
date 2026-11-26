import type { EngineAvailability } from '../ports/engine-availability';
import { harnessLogMeta, HarnessLogFields, HarnessLogger } from './harness-log';

/** Where a claim or a child admission reads the engine image digests it pins (C2.20). */
export type EngineImageSource = Required<
  Pick<EngineAvailability, 'imageDigests'>
>;

/**
 * The broker's image digests for a run being pinned; null when there is no
 * source or the broker could not be asked. A broker that cannot answer never
 * holds a run back: the run pins no image and its worker runs the image the
 * broker has, as before C2.20, and the gap is logged.
 */
export async function engineImageDigests(
  source: EngineImageSource | null,
  logger: HarnessLogger,
  fields: HarnessLogFields & { invocationId?: string },
): Promise<ReadonlyMap<string, string> | null> {
  if (source === null) return null;
  try {
    return await source.imageDigests();
  } catch (error) {
    logger.warn(
      'run.image_unpinned',
      harnessLogMeta('run.image_unpinned', {
        ...fields,
        reason: 'broker_unanswered',
        errorType: error instanceof Error ? error.name : typeof error,
      }),
    );
    return null;
  }
}
