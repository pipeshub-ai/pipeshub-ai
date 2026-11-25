import {
  installationSchema,
  type Installation,
} from '../../../../modules/control-plane/domain/objects';
import {
  ObjectExists,
  ObjectNotFound,
} from '../../../../modules/control-plane/domain/store-errors';
import type {
  InstallationFilter,
  InstallationRevision,
  InstallationStore,
} from '../../../../modules/control-plane/ports/installation-store';
import type { StoreTx } from '../../../../modules/control-plane/ports/store-tx';
import {
  checkedObject,
  compareAndSet,
} from '../../../../modules/control-plane/adapters/store-checks';
import {
  rowRegion,
  type PgKernelTx,
} from '../../agent-harness/pg-documents/pg';
import { Params } from '../../pg-util';
import { cpUnit } from '../pg-units';

const parse = (body: string): Installation =>
  installationSchema.parse(JSON.parse(body));

async function appendHistory(k: PgKernelTx, row: Installation): Promise<void> {
  await k.query(
    `/* pipeshub:harness.cp_installation_history_append */ INSERT INTO harness.cp_installation_history
       (org_id, id, revision, body, region) VALUES ($1, $2, $3, $4, $5)`,
    [row.orgId, row.id, row.revision, JSON.stringify(row), rowRegion()],
  );
}

/** `InstallationStore` over `harness.cp_installations`; every write also appends its revision to `cp_installation_history`. */
export class PgInstallationStore implements InstallationStore {
  get(orgId: string, id: string): Promise<Installation | null> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{ body: string }>(
        `/* pipeshub:harness.cp_installation_get */ SELECT body FROM harness.cp_installations WHERE org_id = $1 AND id = $2`,
        [orgId, id],
      );
      return rows[0] === undefined ? null : parse(rows[0].body);
    });
  }

  listForOrg(
    orgId: string,
    filter: InstallationFilter,
  ): Promise<Installation[]> {
    const p = new Params();
    const where = [`org_id = ${p.add(orgId)}`];
    if (filter.status !== undefined)
      where.push(`status = ANY(${p.add([...filter.status])}::text[])`);
    if (filter.kind !== undefined) where.push(`kind = ${p.add(filter.kind)}`);
    if (filter.owner !== undefined)
      where.push(`owner = ${p.add(filter.owner)}`);
    return this.find(
      orgId,
      `/* pipeshub:harness.cp_installation_list */ SELECT body FROM harness.cp_installations
      WHERE ${where.join(' AND ')} ORDER BY id`,
      p.values,
    );
  }

  async create(tx: StoreTx, inst: Installation): Promise<void> {
    const row = {
      ...checkedObject(tx, installationSchema, inst, `installation ${inst.id}`),
      revision: 1,
    };
    await tx.enlist(() =>
      cpUnit(tx.session, tx.orgId, { write: true }, async (k) => {
        const inserted = await k.query(
          `/* pipeshub:harness.cp_installation_insert */ INSERT INTO harness.cp_installations
             (org_id, id, item, kind, owner, status, installed_via, revision, body, region)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) ON CONFLICT (org_id, id) DO NOTHING`,
          [
            row.orgId,
            row.id,
            row.item,
            row.kind,
            row.owner,
            row.status,
            row.installedVia,
            row.revision,
            JSON.stringify(row),
            rowRegion(),
          ],
        );
        if (inserted.rowCount !== 1)
          throw new ObjectExists(`installation ${row.id} exists`);
        await appendHistory(k, row);
      }),
    );
  }

  async replace(
    tx: StoreTx,
    inst: Installation,
    expectedRevision: number,
  ): Promise<number> {
    const checked = checkedObject(
      tx,
      installationSchema,
      inst,
      `installation ${inst.id}`,
    );
    const row = { ...checked, revision: expectedRevision + 1 };
    await tx.enlist(() =>
      cpUnit(tx.session, tx.orgId, { write: true }, async (k) => {
        const moved = await k.query(
          `/* pipeshub:harness.cp_installation_replace */ UPDATE harness.cp_installations
              SET item = $4, kind = $5, owner = $6, status = $7, installed_via = $8, revision = $9, body = $10
            WHERE org_id = $1 AND id = $2 AND revision = $3`,
          [
            row.orgId,
            row.id,
            expectedRevision,
            row.item,
            row.kind,
            row.owner,
            row.status,
            row.installedVia,
            row.revision,
            JSON.stringify(row),
          ],
        );
        if (moved.rowCount !== 1) {
          const { rows } = await k.query<{ revision: string }>(
            `/* pipeshub:harness.cp_installation_revision */ SELECT revision::text FROM harness.cp_installations WHERE org_id = $1 AND id = $2`,
            [row.orgId, row.id],
          );
          if (rows[0] === undefined)
            throw new ObjectNotFound(`installation ${row.id}`);
          compareAndSet(
            Number(rows[0].revision),
            expectedRevision,
            `installation ${row.id}`,
          );
        }
        await appendHistory(k, row);
      }),
    );
    return row.revision;
  }

  history(
    orgId: string,
    id: string,
    limit: number,
  ): Promise<InstallationRevision[]> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{ revision: string; body: string }>(
        `/* pipeshub:harness.cp_installation_history_read */ SELECT revision::text, body FROM harness.cp_installation_history
          WHERE org_id = $1 AND id = $2 ORDER BY revision DESC LIMIT $3`,
        [orgId, id, limit],
      );
      return rows.map((row) => ({
        revision: Number(row.revision),
        installation: parse(row.body),
      }));
    });
  }

  children(orgId: string, parent: string): Promise<Installation[]> {
    return this.find(
      orgId,
      `/* pipeshub:harness.cp_installation_children */ SELECT body FROM harness.cp_installations
        WHERE org_id = $1 AND installed_via = $2 ORDER BY id`,
      [orgId, parent],
    );
  }

  private find(
    orgId: string,
    text: string,
    values: unknown[],
  ): Promise<Installation[]> {
    return cpUnit(undefined, orgId, { write: false }, async (k) =>
      (await k.query<{ body: string }>(text, values)).rows.map((row) =>
        parse(row.body),
      ),
    );
  }
}
