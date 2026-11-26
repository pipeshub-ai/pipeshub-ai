import {
  CONFIG_CHANGED_KIND,
  ConfigAuditLog,
  ConfigAuditReader,
  ConfigChanged,
  configStreamId,
  CONTROL_PLANE_WRITER,
  CpTx,
  summarizePaths,
} from '../../control-plane';
import { canonicalDigest } from '../contracts/canonical-json';
import { AuditEntry, AuditFields } from '../domain/audit';
import { AuditChainStore } from '../ports/audit';

const MAX_CHANGED_LENGTH = 256;
/** How far back `recorded` looks: a repair only ever completes the org's latest intent. */
const RECORDED_WINDOW = 64;

/** The chain entry of a config change: ids, revisions, digests and field paths, never values. */
export function configAuditEntry(changed: ConfigChanged): AuditEntry {
  const fields: AuditFields = {
    scope: changed.scope,
    objectType: changed.object.type,
    objectId: changed.object.id,
    changeDigest: changed.changeDigest,
    actor: changed.actor,
    headRevision: changed.headRevision,
    changed: summarizePaths(changed.changed, MAX_CHANGED_LENGTH),
  };
  if (changed.revisionFrom !== null)
    fields['revisionFrom'] = changed.revisionFrom;
  if (changed.revisionTo !== null) fields['revisionTo'] = changed.revisionTo;
  if (changed.approvalRef !== null) fields['approvalRef'] = changed.approvalRef;
  if (changed.beforeDigest != null)
    fields['beforeDigest'] = changed.beforeDigest;
  if (changed.repaired === true) fields['repaired'] = true;
  return {
    kind: CONFIG_CHANGED_KIND,
    sessionId: null,
    streamId: configStreamId(changed.orgId),
    runId: null,
    journalSeq: changed.headRevision,
    writer: CONTROL_PLANE_WRITER,
    attemptId: null,
    fence: null,
    fields,
    dataDigest: canonicalDigest(changed),
  };
}

/**
 * `ConfigAuditLog` over the org's audit chain (02-lld §7): config changes are
 * a stream of their own (`cp:{orgId}`) in the same chain as the run journal,
 * so one `verifyAuditChain` covers both.
 */
export class ChainConfigAuditLog<Tx>
  implements ConfigAuditLog, ConfigAuditReader
{
  constructor(
    private readonly chain: AuditChainStore<Tx>,
    private readonly now: () => Date = () => new Date(),
  ) {}

  record(tx: CpTx, changed: ConfigChanged): Promise<void> {
    return tx.enlist(async () => {
      await this.chain.append(
        changed.orgId,
        [configAuditEntry(changed)],
        this.now().toISOString(),
        tx.session as Tx,
      );
    });
  }

  async recorded(orgId: string, headRevision: number): Promise<boolean> {
    const head = await this.chain.head(orgId);
    if (head === null) return false;
    const tail = await this.chain.read(
      orgId,
      Math.max(0, head.seq - RECORDED_WINDOW),
      RECORDED_WINDOW,
    );
    const stream = configStreamId(orgId);
    return tail.some(
      (record) =>
        record.streamId === stream &&
        record.kind === CONFIG_CHANGED_KIND &&
        record.journalSeq === headRevision,
    );
  }
}
