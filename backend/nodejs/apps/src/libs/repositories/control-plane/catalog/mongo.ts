import type { ClientSession, QueryFilter } from 'mongoose';
import { escapeRegExp } from '../../../../utils/escape-regexp';
import { isDuplicateKeyError } from '../../../utils/mongo.utils';
import {
  CatalogItem,
  catalogItemSchema,
} from '../../../../modules/control-plane/domain/catalog-item';
import { ItemExists } from '../../../../modules/control-plane/domain/catalog-errors';
import {
  CatalogFilter,
  CatalogSortKey,
  nameKey,
} from '../../../../modules/control-plane/domain/catalog-order';
import type {
  CatalogWriteTx,
  OrgCatalogRows,
} from '../../../../modules/control-plane/ports/catalog-store';
import { CatalogItemDocument, CatalogItemModel } from './schema/catalog.schema';

const idOf = (orgId: string, ref: string): string => `${orgId}|${ref}`;
const sessionOf = (tx: CatalogWriteTx): ClientSession | undefined =>
  (tx.session ?? undefined) as ClientSession | undefined;
const ORDER = { kind: 1, sortName: 1, ref: 1 } as const;

function toDocument(orgId: string, item: CatalogItem): CatalogItemDocument {
  const { deprecated, ...body } = item;
  return {
    _id: idOf(orgId, item.ref),
    orgId,
    ref: item.ref,
    kind: item.kind,
    publisher: item.publisher,
    name: item.name,
    version: item.version,
    sortName: nameKey(item),
    createdAt: item.createdAt,
    deprecated,
    body: JSON.stringify(body),
  };
}

const fromDocument = (doc: CatalogItemDocument): CatalogItem =>
  catalogItemSchema.parse({
    ...(JSON.parse(doc.body) as object),
    deprecated: doc.deprecated,
  });

function afterKey([
  kind,
  sortName,
  ref,
]: CatalogSortKey): QueryFilter<CatalogItemDocument> {
  return {
    $or: [
      { kind: { $gt: kind } },
      { kind, sortName: { $gt: sortName } },
      { kind, sortName, ref: { $gt: ref } },
    ],
  };
}

/** An org's catalog rows in MongoDB; writes go through the control-plane transaction's session. */
export class MongoOrgCatalogStore implements OrgCatalogRows {
  async get(orgId: string, ref: string): Promise<CatalogItem | null> {
    const doc = await CatalogItemModel.findOne({ _id: idOf(orgId, ref) })
      .lean<CatalogItemDocument>()
      .exec();
    return doc === null ? null : fromDocument(doc);
  }

  async page(
    orgId: string,
    filter: CatalogFilter,
    after: CatalogSortKey | null,
    limit: number,
  ): Promise<CatalogItem[]> {
    const query: QueryFilter<CatalogItemDocument> = {
      orgId,
      ...(after === null ? {} : afterKey(after)),
    };
    if (filter.kind !== undefined) query.kind = filter.kind;
    if (filter.q !== undefined)
      query.sortName = { $regex: escapeRegExp(filter.q.toLowerCase()) };
    const docs = await CatalogItemModel.find(query)
      .sort(ORDER)
      .limit(limit)
      .lean<CatalogItemDocument[]>()
      .exec();
    return docs.map(fromDocument);
  }

  async versions(
    orgId: string,
    kind: string,
    publisher: string,
    name: string,
  ): Promise<CatalogItem[]> {
    const docs = await CatalogItemModel.find({ orgId, kind, publisher, name })
      .sort({ createdAt: 1, ...ORDER })
      .lean<CatalogItemDocument[]>()
      .exec();
    return docs.map(fromDocument);
  }

  async insert(tx: CatalogWriteTx, item: CatalogItem): Promise<void> {
    await tx.enlist(async () => {
      try {
        await CatalogItemModel.create([toDocument(tx.orgId, item)], {
          session: sessionOf(tx),
        });
      } catch (error) {
        if (isDuplicateKeyError(error))
          throw new ItemExists(`${item.ref} exists`);
        throw error;
      }
    });
  }

  async deprecate(
    tx: CatalogWriteTx,
    orgId: string,
    ref: string,
  ): Promise<boolean> {
    const _id = idOf(orgId, ref);
    const session = sessionOf(tx);
    if (
      (await CatalogItemModel.exists({ _id }).session(session ?? null)) === null
    )
      return false;
    await tx.enlist(async () => {
      await CatalogItemModel.updateOne(
        { _id },
        { $set: { deprecated: true } },
        { session },
      ).exec();
    });
    return true;
  }
}
