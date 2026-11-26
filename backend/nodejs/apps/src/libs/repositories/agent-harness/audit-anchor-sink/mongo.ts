import { AuditAnchor } from '../../../../modules/agent-harness/domain/audit';
import { AuditAnchorConflictError } from '../../../../modules/agent-harness/domain/errors';
import { AuditAnchorSink } from '../../../../modules/agent-harness/ports/audit';
import { AuditAnchorLookup } from '../../../../modules/agent-harness/ports/audit-queries';
import { AgentAuditAnchorModel } from '../audit/schema/audit.schema';

const isDuplicateKey = (error: unknown): boolean =>
  typeof error === 'object' &&
  error !== null &&
  (error as { code?: unknown }).code === 11000;

/**
 * The local anchor sink: insert-only documents in their own collection. It
 * catches a rewritten chain that forgot its anchors, not an operator with
 * write access to both; an edition may anchor to write-once storage
 * instead (`HarnessEdition.auditAnchors`).
 */
export class MongoAuditAnchorSink
  implements AuditAnchorSink, AuditAnchorLookup
{
  readonly name = 'mongo';

  async write(anchor: AuditAnchor): Promise<void> {
    try {
      await new AgentAuditAnchorModel({
        _id: `${anchor.orgId}:${String(anchor.seq)}`,
        ...anchor,
      }).save({ w: 'majority' });
    } catch (error) {
      if (!isDuplicateKey(error)) throw error;
      const existing = await AgentAuditAnchorModel.findById(
        `${anchor.orgId}:${String(anchor.seq)}`,
      )
        .read('primary')
        .lean();
      if (existing?.hash !== anchor.hash) {
        throw new AuditAnchorConflictError(anchor.orgId, anchor.seq);
      }
    }
  }

  async atOrAfter(orgId: string, seq: number): Promise<AuditAnchor | null> {
    const anchor = await AgentAuditAnchorModel.findOne({
      orgId,
      seq: { $gte: seq },
    })
      .sort({ seq: 1 })
      .read('primary')
      .readConcern('majority')
      .lean();
    return anchor === null
      ? null
      : {
          orgId: anchor.orgId,
          seq: anchor.seq,
          hash: anchor.hash,
          anchoredAt: anchor.anchoredAt,
        };
  }

  async list(orgId: string): Promise<AuditAnchor[]> {
    const anchors = await AgentAuditAnchorModel.find({ orgId })
      .sort({ seq: 1 })
      .read('primary')
      .readConcern('majority')
      .lean();
    return anchors.map((anchor) => ({
      orgId: anchor.orgId,
      seq: anchor.seq,
      hash: anchor.hash,
      anchoredAt: anchor.anchoredAt,
    }));
  }
}
