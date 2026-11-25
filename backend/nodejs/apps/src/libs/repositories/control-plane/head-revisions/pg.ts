import type {
  DeploymentRevision,
  HeadRevisions,
  Heads,
} from '../../../../modules/control-plane/ports/head-revisions';
import type { CpTx } from '../../../../modules/control-plane/ports/transaction';
import type { Tx } from '../../uow';
import { cpUnit } from '../pg-units';

/**
 * Head reads on PostgreSQL: the org's row in `harness.cp_org_heads` and the deployment's in the internal
 * `harness.cp_deployment_head` (no org owns it, so it carries no org_id; any org's unit reads and moves it), in one
 * statement.
 */
export class PgHeadRevisions implements HeadRevisions {
  async head(orgId: string): Promise<number> {
    return (await this.heads(orgId)).org;
  }

  /** `session`: read inside that unit, as an admission reads them where it writes its run. */
  heads(orgId: string, session?: Tx): Promise<Heads> {
    return cpUnit(session, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{ org: string; deployment: string }>(
        `/* pipeshub:harness.cp_heads_read */ SELECT
           COALESCE((SELECT revision FROM harness.cp_org_heads WHERE org_id = $1), 0)::text AS org,
           COALESCE((SELECT revision FROM harness.cp_deployment_head WHERE id = 'deployment'), 0)::text AS deployment`,
        [orgId],
      );
      return {
        org: Number(rows[0]?.org ?? 0),
        deployment: Number(rows[0]?.deployment ?? 0),
      };
    });
  }
}

/** The deployment's revision, moved in the writing org's unit (its row lock is held to that unit's commit). */
export class PgDeploymentRevision implements DeploymentRevision {
  move(tx: CpTx): Promise<void> {
    return tx.enlist(() =>
      cpUnit(tx.session, tx.orgId, { write: true }, async (k) => {
        await k.query(
          `/* pipeshub:harness.cp_deployment_head_move */ INSERT INTO harness.cp_deployment_head AS d (id, revision, updated_at)
             VALUES ('deployment', 1, now())
           ON CONFLICT (id) DO UPDATE SET revision = d.revision + 1, updated_at = now()`,
        );
      }),
    );
  }
}
