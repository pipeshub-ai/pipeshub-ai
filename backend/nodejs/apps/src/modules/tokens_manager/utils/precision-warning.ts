import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';

/** Written by the indexing service while it waits for the embedding server's weight precision to match the index. */
export const PRECISION_VERDICT_KEY = '/services/indexing/precision_verdict';

interface PrecisionVerdict {
  mismatch?: boolean;
  /** The precisions the disagreeing indexes hold: `fp32` or `int8-w`. */
  holds?: string[];
}

/** What to set EMBEDDING_SERVER_PRECISION to so that the embedding server serves what the index holds. */
function settingFor(holds: string[] | undefined): string {
  if (holds?.length === 1 && holds[0] === 'int8-w')
    return 'EMBEDDING_SERVER_PRECISION=int8';
  if (holds?.length === 1 && holds[0] === 'fp32') {
    return 'EMBEDDING_SERVER_PRECISION=fp32 (or remove the setting, which means fp32)';
  }
  return 'EMBEDDING_SERVER_PRECISION to the value the index was built with';
}

/** Server text stays in English; the page shows it as it is. */
export function precisionWaitWarning(verdict: PrecisionVerdict): string {
  return (
    'Indexing is waiting: the embedding server stores its model weights at a different precision than the search index was built with. ' +
    `To continue, set ${settingFor(verdict.holds)} and restart the embedding server, ` +
    'or, to keep the new setting, open Workspace > Connectors, run "Clean up embeddings" and then "Reindex embeddings". ' +
    'Cleaning up deletes the whole search index, and search finds nothing until the reindex has finished.'
  );
}

/** The warning while a fresh verdict says "mismatch"; null otherwise. The key expires on its own, so a stopped service shows nothing. */
export async function precisionWaitWarningFromStore(
  keyValueStoreService: KeyValueStoreService,
): Promise<string | null> {
  const raw = await keyValueStoreService.get<string | PrecisionVerdict>(
    PRECISION_VERDICT_KEY,
  );
  if (!raw) return null;
  const verdict =
    typeof raw === 'string' ? (JSON.parse(raw) as PrecisionVerdict) : raw;
  return verdict.mismatch === true ? precisionWaitWarning(verdict) : null;
}
