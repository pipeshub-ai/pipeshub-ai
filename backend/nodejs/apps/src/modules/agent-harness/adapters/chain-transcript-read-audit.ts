import {
  transcriptReadEntries,
  TranscriptRead,
} from '../domain/transcript-read-audit';
import { AuditChainStore } from '../ports/audit';

/**
 * Reads of a run's transcript by someone outside its session (SEC-LG-02), on
 * the org's audit chain as downloads are (`ChainWorkspaceDownloadAudit`): all
 * of one answer's entries in one transaction, committed before the answer is
 * sent.
 */
export class ChainTranscriptReadAudit<Tx> {
  constructor(
    private readonly chain: Pick<AuditChainStore<Tx>, 'append'>,
    private readonly transaction: <T>(
      work: (tx: Tx) => Promise<T>,
    ) => Promise<T>,
    private readonly now: () => Date = () => new Date(),
  ) {}

  readonly record = async (read: TranscriptRead): Promise<void> => {
    if (read.runs.length === 0) return;
    const at = this.now();
    const entries = transcriptReadEntries(read, at.getTime());
    await this.transaction((tx) =>
      this.chain.append(read.orgId, entries, at.toISOString(), tx),
    );
  };
}
