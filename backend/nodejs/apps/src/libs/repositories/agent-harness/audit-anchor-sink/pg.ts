import type { AuditAnchor } from '../../../../modules/agent-harness/domain/audit';
import { AuditAnchorConflictError } from '../../../../modules/agent-harness/domain/errors';
import type { AuditAnchorSink } from '../../../../modules/agent-harness/ports/audit';
import type { AuditAnchorLookup } from '../../../../modules/agent-harness/ports/audit-queries';
import { rowRegion } from '../pg-documents/pg';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';

interface AnchorRow {
  org_id: string;
  seq: string;
  hash: string;
  anchored_at: string;
}

const anchorOf = (row: AnchorRow): AuditAnchor => ({
  orgId: row.org_id,
  seq: Number(row.seq),
  hash: row.hash,
  anchoredAt: row.anchored_at,
});

/**
 * The local anchor sink on PostgreSQL (`harness.audit_anchors`): insert-only rows. A second write of an anchored seq
 * keeps the first row (`ON CONFLICT DO NOTHING`) and is refused when its hash differs. It catches a rewritten chain
 * that forgot its anchors, not an operator with write access to both; an edition may anchor to write-once storage
 * instead (`HarnessEdition.auditAnchors`).
 */
export class PgAuditAnchorSink implements AuditAnchorSink, AuditAnchorLookup {
  readonly name = 'postgres';

  async write(anchor: AuditAnchor): Promise<void> {
    const stored = await orgOrJobsUnit(
      undefined,
      anchor.orgId,
      'audit-anchor',
      { write: true },
      async (k) => {
        await k.query(
          `/* pipeshub:harness.audit_anchors_write */ INSERT INTO harness.audit_anchors
           (org_id, seq, hash, anchored_at, region) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (org_id, seq) DO NOTHING`,
          [
            anchor.orgId,
            anchor.seq,
            anchor.hash,
            anchor.anchoredAt,
            rowRegion(),
          ],
        );
        const { rows } = await k.query<{ hash: string }>(
          `/* pipeshub:harness.audit_anchors_hash */ SELECT hash FROM harness.audit_anchors WHERE org_id = $1 AND seq = $2`,
          [anchor.orgId, anchor.seq],
        );
        return rows[0]?.hash;
      },
    );
    if (stored !== anchor.hash)
      throw new AuditAnchorConflictError(anchor.orgId, anchor.seq);
  }

  atOrAfter(orgId: string, seq: number): Promise<AuditAnchor | null> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      'audit-anchor',
      { write: false },
      async (k) => {
        const { rows } = await k.query<AnchorRow>(
          `/* pipeshub:harness.audit_anchors_at_or_after */ SELECT org_id, seq, hash, anchored_at
           FROM harness.audit_anchors WHERE org_id = $1 AND seq >= $2 ORDER BY seq LIMIT 1`,
          [orgId, seq],
        );
        return rows[0] === undefined ? null : anchorOf(rows[0]);
      },
    );
  }

  list(orgId: string): Promise<AuditAnchor[]> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      'audit-anchor',
      { write: false },
      async (k) => {
        const { rows } = await k.query<AnchorRow>(
          `/* pipeshub:harness.audit_anchors_list */ SELECT org_id, seq, hash, anchored_at
           FROM harness.audit_anchors WHERE org_id = $1 ORDER BY seq`,
          [orgId],
        );
        return rows.map(anchorOf);
      },
    );
  }
}
