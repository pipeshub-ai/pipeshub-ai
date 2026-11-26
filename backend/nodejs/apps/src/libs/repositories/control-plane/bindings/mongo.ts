import { assertIdFilter, eq } from '../../id-filter-guard/mongo';
import { MaxKey } from 'mongodb';
import type { ClientSession, QueryFilter } from 'mongoose';
import { isDuplicateKeyError } from '../../../utils/mongo.utils';
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
  BINDING_INSTALLATION_INDEX,
  CpBindingModel,
  type BindingDocument,
} from './schema/bindings.schema';

const sessionOf = (tx: StoreTx): ClientSession | undefined =>
  (tx.session ?? undefined) as ClientSession | undefined;

const LIVE: ReadonlyArray<Binding['status']> = ['active', 'expiring'];

const fromDocument = ({
  _id,
  refreshClaim: _claim,
  ...rest
}: BindingDocument): Binding => bindingSchema.parse({ ...rest, id: _id });

/** No claim, an expired one, or the claimant's own. */
const claimableBy = ({
  holder,
  now,
}: RefreshClaimant): QueryFilter<BindingDocument> => ({
  $or: [
    { refreshClaim: null },
    { 'refreshClaim.until': { $lte: now } },
    { 'refreshClaim.holder': holder },
  ],
});

const toDocument = ({ id, ...rest }: Binding): BindingDocument => ({
  _id: id,
  ...rest,
});

/**
 * `BindingStore` over `cpBindings`. One binding that is not revoked per
 * `(org, installation, owner)` is the partial unique index's, so two creates
 * racing in two transactions cannot both land; updates are compare-and-set on
 * `revision`.
 */
export class MongoBindingStore implements BindingStore {
  async get(orgId: string, id: string): Promise<Binding | null> {
    const doc = await CpBindingModel.findOne({ _id: id, orgId })
      .lean<BindingDocument>()
      .exec();
    return doc === null ? null : fromDocument(doc);
  }

  async find(
    orgId: string,
    installation: string,
    owner: BindingOwnerRef,
  ): Promise<Binding | null> {
    // The one that is not revoked sorts first; among revoked ones, the latest (ids are ULIDs).
    const docs = await CpBindingModel.find({
      orgId: eq(orgId),
      installation: eq(installation),
      owner: eq(owner),
    })
      .sort({ _id: -1 })
      .lean<BindingDocument[]>()
      .exec();
    const doc = docs.find(isLiveBinding) ?? docs[0];
    return doc === undefined ? null : fromDocument(doc);
  }

  async listForOwner(
    orgId: string,
    owner: BindingOwnerRef,
  ): Promise<Binding[]> {
    const docs = await CpBindingModel.find({ orgId, owner })
      .sort({ _id: 1 })
      .lean<BindingDocument[]>()
      .exec();
    return docs.map(fromDocument);
  }

  async listForOwners(
    orgId: string,
    owners: readonly BindingOwnerRef[],
  ): Promise<Binding[]> {
    if (owners.length === 0) return [];
    const docs = await CpBindingModel.find({ orgId, owner: { $in: owners } })
      .sort({ _id: 1 })
      .lean<BindingDocument[]>()
      .exec();
    return owners.flatMap((owner) =>
      docs.filter((doc) => doc.owner === owner).map(fromDocument),
    );
  }

  /**
   * Read in index order from `cp_bindings_installation` (orgId, installation,
   * owner, _id), starting at the cursor's own key: an `$or` on (owner, _id)
   * would make the planner sort. The cursor's row comes back first and is
   * left out.
   */
  async listByInstallation(
    orgId: string,
    installation: string,
    page: PageRequest,
  ): Promise<Page<Binding>> {
    const after = page.after === null ? null : bindingCursorParts(page.after);
    // The driver directly, past the model's query middleware.
    assertIdFilter({ orgId, installation });
    const docs = await CpBindingModel.collection
      .find<BindingDocument>(
        { orgId, installation },
        {
          hint: BINDING_INSTALLATION_INDEX,
          sort: { owner: 1, _id: 1 },
          limit: page.limit + 2,
          ...(after === null
            ? {}
            : {
                min: { orgId, installation, owner: after.owner, _id: after.id },
                max: {
                  orgId,
                  installation,
                  owner: new MaxKey(),
                  _id: new MaxKey(),
                },
              }),
        },
      )
      .toArray();
    const rest = docs.filter((doc) => doc._id !== after?.id);
    const items = rest.slice(0, page.limit).map(fromDocument);
    const last = items.at(-1);
    return {
      items,
      next:
        rest.length > page.limit && last !== undefined
          ? bindingCursor(last)
          : null,
    };
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
    const doc = toDocument({ ...checked, revision });
    const what = `binding ${checked.id}`;
    // A duplicate key is the `_id`, or the unique index over the owner's binding that is not revoked.
    const taken = (error: unknown): unknown =>
      isDuplicateKeyError(error)
        ? new ObjectExists(
            `a binding of ${checked.installation} for ${checked.owner} exists`,
          )
        : error;
    await tx.enlist(async () => {
      const session = sessionOf(tx);
      if (expectedRevision === null) {
        try {
          await CpBindingModel.create([doc], { session });
        } catch (error) {
          throw taken(error);
        }
        return;
      }
      let matchedCount: number;
      try {
        ({ matchedCount } = await CpBindingModel.updateOne(
          { _id: checked.id, orgId: tx.orgId, revision: expectedRevision },
          { $set: doc, $unset: { refreshClaim: 1 } },
          { session },
        ).exec());
      } catch (error) {
        throw taken(error);
      }
      if (matchedCount > 0) return;
      const current = await CpBindingModel.findOne(
        { _id: checked.id, orgId: tx.orgId },
        { revision: 1 },
      )
        .session(session ?? null)
        .lean<Pick<BindingDocument, 'revision'>>()
        .exec();
      if (current === null) throw new ObjectNotFound(what);
      compareAndSet(current.revision, expectedRevision, what);
    });
    return revision;
  }

  async remove(
    tx: StoreTx,
    id: string,
    expectedRevision: number,
  ): Promise<void> {
    const what = `binding ${id}`;
    await tx.enlist(async () => {
      const session = sessionOf(tx);
      const { deletedCount } = await CpBindingModel.deleteOne(
        { _id: id, orgId: tx.orgId, revision: expectedRevision },
        { session },
      ).exec();
      if (deletedCount > 0) return;
      const current = await CpBindingModel.findOne(
        { _id: id, orgId: tx.orgId },
        { revision: 1 },
      )
        .session(session ?? null)
        .lean<Pick<BindingDocument, 'revision'>>()
        .exec();
      if (current === null) throw new ObjectNotFound(what);
      compareAndSet(current.revision, expectedRevision, what);
    });
  }

  async dueForRefresh(
    before: number,
    limit: number,
    claimable?: RefreshClaimant,
  ): Promise<Binding[]> {
    const docs = await CpBindingModel.find({
      status: { $in: LIVE },
      expiresAt: { $ne: null, $lt: before },
      ...(claimable === undefined ? {} : claimableBy(claimable)),
    })
      .sort({ expiresAt: 1, _id: 1 })
      .limit(limit)
      .lean<BindingDocument[]>()
      .exec();
    return docs.map(fromDocument);
  }

  async claimRefresh(
    orgId: string,
    id: string,
    revision: number,
    claim: RefreshClaimant & { readonly until: number },
  ): Promise<boolean> {
    const { matchedCount } = await CpBindingModel.updateOne(
      { _id: id, orgId, revision, ...claimableBy(claim) },
      { $set: { refreshClaim: { holder: claim.holder, until: claim.until } } },
    ).exec();
    return matchedCount > 0;
  }
}
