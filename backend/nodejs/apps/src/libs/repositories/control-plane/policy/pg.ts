import {
  ControlPlaneRevisionConflictError,
  ControlPlaneUnavailableError,
} from '../../../../modules/control-plane/domain/errors';
import type { PolicyLayer } from '../../../../modules/control-plane/domain/policy-alias';
import {
  readPolicyLayer,
  scopeKindOf,
  writeEach,
} from '../../../../modules/control-plane/domain/policy-layer';
import { InvalidObject } from '../../../../modules/control-plane/domain/store-errors';
import type {
  LayerRead,
  LayerWrite,
  PolicyLayers,
  PolicyOrgSource,
  PolicyRevision,
  PolicyStore,
  PolicyStorePurge,
  VersionedLayer,
} from '../../../../modules/control-plane/ports/policy-store';
import type { StoreTx } from '../../../../modules/control-plane/ports/store-tx';
import { policyWritten } from '../../../../modules/control-plane/ports/policy-write-observers';
import {
  jobsUnit,
  rowRegion,
  type PgKernelTx,
} from '../../agent-harness/pg-documents/pg';
import { cpUnit } from '../pg-units';

const ORG_SCAN_PAGE = 500;
const UNAVAILABLE: LayerRead = {
  status: 'unavailable',
  code: 'CP_UNAVAILABLE',
};

function parsed(body: string | null): PolicyLayer | null | undefined {
  if (body === null) return null;
  try {
    return readPolicyLayer(JSON.parse(body)) ?? undefined;
  } catch {
    return undefined;
  }
}

function layerRead(
  row: { body: string | null; revision: string } | undefined,
): LayerRead {
  if (row === undefined) return { status: 'ok', layer: null, revision: 0 };
  const layer = parsed(row.body);
  return layer === undefined
    ? UNAVAILABLE
    : { status: 'ok', layer, revision: Number(row.revision) };
}

async function revisionOf(
  k: PgKernelTx,
  orgId: string,
  scope: string,
): Promise<number> {
  const { rows } = await k.query<{ revision: string }>(
    `/* pipeshub:harness.cp_policy_revision */ SELECT revision::text FROM harness.cp_policy_layers WHERE org_id = $1 AND scope = $2`,
    [orgId, scope],
  );
  return Number(rows[0]?.revision ?? 0);
}

function conflict(scope: string, current: number, expected: number): never {
  throw new ControlPlaneRevisionConflictError(
    `policy layer ${scope} is at revision ${String(current)}, not ${String(expected)}`,
    String(current),
  );
}

/**
 * `PolicyStore` over `harness.cp_policy_layers` and `cp_policy_history`. A write is one guarded statement (an insert
 * that does nothing when the scope exists, or an update at the expected revision), so of two writers at one revision
 * the second waits on the row and then finds it moved: a revision conflict, never a lost update.
 */
export class PgPolicyStore
  implements PolicyStore, PolicyLayers, PolicyStorePurge
{
  async read(orgId: string, scope: string): Promise<VersionedLayer> {
    const found = (await this.readMany(orgId, [scope])).get(scope);
    if (found === undefined || found.status === 'unavailable') {
      throw new ControlPlaneUnavailableError(
        `the stored policy layer of ${scope} does not parse`,
      );
    }
    return { layer: found.layer, revision: found.revision };
  }

  readMany(
    orgId: string,
    scopes: readonly string[],
  ): Promise<Map<string, LayerRead>> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{
        scope: string;
        body: string | null;
        revision: string;
      }>(
        `/* pipeshub:harness.cp_policy_read */ SELECT scope, body, revision::text FROM harness.cp_policy_layers
          WHERE org_id = $1 AND scope = ANY($2::text[])`,
        [orgId, [...scopes]],
      );
      const byScope = new Map(rows.map((row) => [row.scope, row]));
      return new Map(
        scopes.map((scope) => [scope, layerRead(byScope.get(scope))]),
      );
    });
  }

  async write(
    tx: StoreTx,
    scope: string,
    layer: PolicyLayer | null,
    expectedRevision: number,
  ): Promise<number> {
    if (layer !== null && readPolicyLayer(layer) === null) {
      throw new InvalidObject(`the policy layer for ${scope} does not parse`);
    }
    const revision = expectedRevision + 1;
    const body = layer === null ? null : JSON.stringify(layer);
    await tx.enlist(() =>
      cpUnit(tx.session, tx.orgId, { write: true }, async (k) => {
        const moved =
          expectedRevision === 0
            ? await k.query(
                `/* pipeshub:harness.cp_policy_insert */ INSERT INTO harness.cp_policy_layers
                   (org_id, scope, kind, revision, body, updated_at, region) VALUES ($1, $2, $3, 1, $4, now(), $5)
                 ON CONFLICT (org_id, scope) DO NOTHING`,
                [tx.orgId, scope, scopeKindOf(scope), body, rowRegion()],
              )
            : await k.query(
                `/* pipeshub:harness.cp_policy_update */ UPDATE harness.cp_policy_layers
                    SET revision = $4, body = $5, updated_at = now()
                  WHERE org_id = $1 AND scope = $2 AND revision = $3`,
                [tx.orgId, scope, expectedRevision, revision, body],
              );
        if (moved.rowCount !== 1)
          conflict(
            scope,
            await revisionOf(k, tx.orgId, scope),
            expectedRevision,
          );
        await k.query(
          `/* pipeshub:harness.cp_policy_history_append */ INSERT INTO harness.cp_policy_history
             (org_id, scope, revision, body, at, region) VALUES ($1, $2, $3, $4, now(), $5)`,
          [tx.orgId, scope, revision, body, rowRegion()],
        );
      }),
    );
    await policyWritten(tx, scope, layer);
    return revision;
  }

  history(
    orgId: string,
    scope: string,
    limit: number,
  ): Promise<PolicyRevision[]> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{ revision: string; body: string | null }>(
        `/* pipeshub:harness.cp_policy_history_read */ SELECT revision::text, body FROM harness.cp_policy_history
          WHERE org_id = $1 AND scope = $2 ORDER BY revision DESC LIMIT $3`,
        [orgId, scope, limit],
      );
      // A history row that no longer parses is shown as no layer, never hidden.
      return rows.map((row) => ({
        revision: Number(row.revision),
        layer: parsed(row.body) ?? null,
      }));
    });
  }

  scopesOf(orgId: string, kind: string): Promise<string[]> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{ scope: string }>(
        `/* pipeshub:harness.cp_policy_scopes */ SELECT scope FROM harness.cp_policy_layers
          WHERE org_id = $1 AND kind = $2 AND body IS NOT NULL ORDER BY scope`,
        [orgId, kind],
      );
      return rows.map((row) => row.scope);
    });
  }

  orgSource(): Promise<PolicyOrgSource> {
    return Promise.resolve('store');
  }

  writeMany(
    tx: StoreTx,
    writes: readonly LayerWrite[],
  ): Promise<Map<string, number>> {
    return writeEach(this, tx, writes);
  }

  /** Every org's own layer, a page at a time as the jobs role (the one cross-org read). */
  async *orgLayers(): AsyncIterable<{ orgId: string; layer: PolicyLayer }> {
    let after = '';
    for (;;) {
      const rows = await jobsUnit(
        'control-plane-org-layers',
        { write: false },
        async (k) =>
          (
            await k.query<{ org_id: string; body: string | null }>(
              `/* pipeshub:harness.cp_policy_org_layers */ SELECT org_id, body FROM harness.cp_policy_layers
              WHERE kind = 'org' AND body IS NOT NULL AND org_id > $1 ORDER BY org_id LIMIT $2`,
              [after, ORG_SCAN_PAGE],
            )
          ).rows,
      );
      for (const row of rows) {
        const layer = parsed(row.body);
        if (layer) yield { orgId: row.org_id, layer };
        after = row.org_id;
      }
      if (rows.length < ORG_SCAN_PAGE) return;
    }
  }

  /**
   * One batch of an org's layers removed with the org (CLEANUP-G1, after its grace): no observer is told, since nothing
   * of the org is left to publish. The count removed.
   */
  eraseOrgLayers(orgId: string, limit: number): Promise<number> {
    return cpUnit(undefined, orgId, { write: true }, async (k) => {
      const { rowCount } = await k.query(
        `/* pipeshub:harness.org_erase_cp_policy_layers */ DELETE FROM harness.cp_policy_layers
          WHERE ctid IN (SELECT ctid FROM harness.cp_policy_layers WHERE org_id = $1 LIMIT $2)`,
        [orgId, limit],
      );
      return rowCount ?? 0;
    });
  }

  async purge(tx: StoreTx, orgId: string): Promise<void> {
    await tx.enlist(() =>
      cpUnit(tx.session, orgId, { write: true }, async (k) => {
        await k.query(
          `/* pipeshub:harness.cp_policy_purge */ DELETE FROM harness.cp_policy_layers WHERE org_id = $1`,
          [orgId],
        );
        await k.query(
          `/* pipeshub:harness.cp_policy_history_purge */ DELETE FROM harness.cp_policy_history WHERE org_id = $1`,
          [orgId],
        );
      }),
    );
  }
}
