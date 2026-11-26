import { UniqueConstraintError } from '../../../errors/database.errors';
import {
  bindingSchema,
  type Binding,
  type BindingOwnerRef,
} from '../../../../modules/control-plane/domain/objects';
import {
  bindingCursor,
  bindingCursorParts,
  isLiveBinding,
} from '../../../../modules/control-plane/domain/bindings';
import {
  ObjectExists,
  ObjectNotFound,
} from '../../../../modules/control-plane/domain/store-errors';
import type {
  BindingStore,
  PageRequest,
  RefreshClaimant,
} from '../../../../modules/control-plane/ports/binding-store';
import type {
  Page,
  StoreTx,
} from '../../../../modules/control-plane/ports/store-tx';
import {
  checkedObject,
  compareAndSet,
} from '../../../../modules/control-plane/adapters/store-checks';
import {
  decodeDoc,
  docParam,
  jobsUnit,
  rowRegion,
  type PgKernelTx,
} from '../../agent-harness/pg-documents/pg';
import { cpUnit } from '../pg-units';

const fromRow = (row: { doc: unknown }): Binding =>
  bindingSchema.parse(decodeDoc(row.doc));

/** No claim, an expired one, or the claimant's own (`$n` holder, `$m` now). */
const claimable = (holder: string, now: string): string =>
  `(refresh_holder IS NULL OR refresh_until <= ${now} OR refresh_holder = ${holder})`;

async function revisionOf(
  k: PgKernelTx,
  orgId: string,
  id: string,
): Promise<number | null> {
  const { rows } = await k.query<{ revision: string }>(
    `/* pipeshub:harness.cp_binding_revision */ SELECT revision::text FROM harness.cp_bindings WHERE org_id = $1 AND id = $2`,
    [orgId, id],
  );
  return rows[0] === undefined ? null : Number(rows[0].revision);
}

/**
 * `BindingStore` over `harness.cp_bindings`. One binding that is not revoked per (org, installation, owner) is the
 * partial unique index's, so two creates racing in two units cannot both land; updates are compare-and-set on
 * `revision`, and the refresher's claim is a guarded update.
 */
export class PgBindingStore implements BindingStore {
  get(orgId: string, id: string): Promise<Binding | null> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.cp_binding_get */ SELECT doc FROM harness.cp_bindings WHERE org_id = $1 AND id = $2`,
        [orgId, id],
      );
      return rows[0] === undefined ? null : fromRow(rows[0]);
    });
  }

  find(
    orgId: string,
    installation: string,
    owner: BindingOwnerRef,
  ): Promise<Binding | null> {
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.cp_binding_find */ SELECT doc FROM harness.cp_bindings
          WHERE org_id = $1 AND installation = $2 AND owner = $3 ORDER BY id DESC`,
        [orgId, installation, owner],
      );
      // The one that is not revoked comes first; among revoked ones, the latest (ids are ULIDs).
      const bindings = rows.map(fromRow);
      return bindings.find(isLiveBinding) ?? bindings[0] ?? null;
    });
  }

  listForOwner(orgId: string, owner: BindingOwnerRef): Promise<Binding[]> {
    return this.listForOwners(orgId, [owner]);
  }

  async listForOwners(
    orgId: string,
    owners: readonly BindingOwnerRef[],
  ): Promise<Binding[]> {
    if (owners.length === 0) return [];
    const rows = await cpUnit(undefined, orgId, { write: false }, async (k) =>
      (
        await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.cp_binding_for_owners */ SELECT doc FROM harness.cp_bindings
            WHERE org_id = $1 AND owner = ANY($2::text[]) ORDER BY id`,
          [orgId, [...owners]],
        )
      ).rows.map(fromRow),
    );
    return owners.flatMap((owner) =>
      rows.filter((binding) => binding.owner === owner),
    );
  }

  /** In index order (owner, id) from the cursor's own key; the cursor's row comes back first and is left out. */
  listByInstallation(
    orgId: string,
    installation: string,
    page: PageRequest,
  ): Promise<Page<Binding>> {
    const after = page.after === null ? null : bindingCursorParts(page.after);
    return cpUnit(undefined, orgId, { write: false }, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.cp_binding_by_installation */ SELECT doc FROM harness.cp_bindings
          WHERE org_id = $1 AND installation = $2 AND ($3::text IS NULL OR (owner, id) >= ($3::text, $4::text))
          ORDER BY owner, id LIMIT $5`,
        [
          orgId,
          installation,
          after?.owner ?? null,
          after?.id ?? '',
          page.limit + 2,
        ],
      );
      const rest = rows
        .map(fromRow)
        .filter((binding) => binding.id !== after?.id);
      const items = rest.slice(0, page.limit);
      const last = items.at(-1);
      return {
        items,
        next:
          rest.length > page.limit && last !== undefined
            ? bindingCursor(last)
            : null,
      };
    });
  }

  async upsert(
    tx: StoreTx,
    binding: Binding,
    expectedRevision: number | null,
  ): Promise<number> {
    const checked = checkedObject(
      tx,
      bindingSchema,
      binding,
      `binding ${binding.id}`,
    );
    const revision = (expectedRevision ?? 0) + 1;
    const row = { ...checked, revision };
    const what = `binding ${checked.id}`;
    const taken = (): ObjectExists =>
      new ObjectExists(
        `a binding of ${checked.installation} for ${checked.owner} exists`,
      );
    await tx.enlist(() =>
      cpUnit(tx.session, tx.orgId, { write: true }, async (k) => {
        const values = [
          tx.orgId,
          row.id,
          row.installation,
          row.owner,
          row.status,
          row.expiresAt,
          revision,
          docParam(row),
        ];
        if (expectedRevision === null) {
          const inserted = await k.query(
            `/* pipeshub:harness.cp_binding_insert */ INSERT INTO harness.cp_bindings
               (org_id, id, installation, owner, status, expires_at, revision, doc, region)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9) ON CONFLICT DO NOTHING`,
            [...values, rowRegion()],
          );
          if (inserted.rowCount !== 1) throw taken();
          return;
        }
        let matched: number;
        try {
          // A live status may collide with the owner's other live binding: caught without losing the unit.
          matched = await k.savepoint(
            'cp_binding_update',
            async () =>
              (
                await k.query(
                  `/* pipeshub:harness.cp_binding_update */ UPDATE harness.cp_bindings
                      SET installation = $3, owner = $4, status = $5, expires_at = $6, revision = $7, doc = $8::jsonb,
                          refresh_holder = NULL, refresh_until = NULL
                    WHERE org_id = $1 AND id = $2 AND revision = $9`,
                  [...values, expectedRevision],
                )
              ).rowCount ?? 0,
          );
        } catch (error) {
          if (error instanceof UniqueConstraintError) throw taken();
          throw error;
        }
        if (matched > 0) return;
        const current = await revisionOf(k, tx.orgId, row.id);
        if (current === null) throw new ObjectNotFound(what);
        compareAndSet(current, expectedRevision, what);
      }),
    );
    return revision;
  }

  async remove(
    tx: StoreTx,
    id: string,
    expectedRevision: number,
  ): Promise<void> {
    const what = `binding ${id}`;
    await tx.enlist(() =>
      cpUnit(tx.session, tx.orgId, { write: true }, async (k) => {
        const deleted = await k.query(
          `/* pipeshub:harness.cp_binding_remove */ DELETE FROM harness.cp_bindings WHERE org_id = $1 AND id = $2 AND revision = $3`,
          [tx.orgId, id, expectedRevision],
        );
        if (deleted.rowCount === 1) return;
        const current = await revisionOf(k, tx.orgId, id);
        if (current === null) throw new ObjectNotFound(what);
        compareAndSet(current, expectedRevision, what);
      }),
    );
  }

  /** Live bindings of every org expiring before `before`, as the jobs role (the refresher's scan). */
  dueForRefresh(
    before: number,
    limit: number,
    claimant?: RefreshClaimant,
  ): Promise<Binding[]> {
    return jobsUnit(
      'control-plane-binding-refresh',
      { write: false },
      async (k) => {
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.cp_binding_due */ SELECT doc FROM harness.cp_bindings
          WHERE status IN ('active', 'expiring') AND expires_at IS NOT NULL AND expires_at < $1
            AND ($3::text IS NULL OR ${claimable('$3', '$4')})
          ORDER BY expires_at, id LIMIT $2`,
          [before, limit, claimant?.holder ?? null, claimant?.now ?? 0],
        );
        return rows.map(fromRow);
      },
    );
  }

  claimRefresh(
    orgId: string,
    id: string,
    revision: number,
    claim: RefreshClaimant & { readonly until: number },
  ): Promise<boolean> {
    return cpUnit(undefined, orgId, { write: true }, async (k) => {
      const claimed = await k.query(
        `/* pipeshub:harness.cp_binding_claim */ UPDATE harness.cp_bindings SET refresh_holder = $4, refresh_until = $6
          WHERE org_id = $1 AND id = $2 AND revision = $3 AND ${claimable('$4', '$5')}`,
        [orgId, id, revision, claim.holder, claim.now, claim.until],
      );
      return claimed.rowCount === 1;
    });
  }
}
