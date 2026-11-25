import type { ClientSession, QueryFilter } from 'mongoose';
import { isDuplicateKeyError } from '../../../utils/mongo.utils';
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
  CpInstallationHistoryModel,
  CpInstallationModel,
  type InstallationDocument,
} from './schema/installations.schema';

const rowId = (orgId: string, id: string): string => `${orgId}|${id}`;
const sessionOf = (tx: StoreTx): ClientSession | undefined =>
  (tx.session ?? undefined) as ClientSession | undefined;
const parse = (body: string): Installation =>
  installationSchema.parse(JSON.parse(body));

function toDocument(inst: Installation): InstallationDocument {
  return {
    _id: rowId(inst.orgId, inst.id),
    orgId: inst.orgId,
    id: inst.id,
    item: inst.item,
    kind: inst.kind,
    owner: inst.owner,
    status: inst.status,
    installedVia: inst.installedVia,
    revision: inst.revision,
    body: JSON.stringify(inst),
  };
}

/** `InstallationStore` over `cpInstallations`; every write also appends its revision to `cpInstallationHistory`. */
export class MongoInstallationStore implements InstallationStore {
  async get(orgId: string, id: string): Promise<Installation | null> {
    const doc = await CpInstallationModel.findById(rowId(orgId, id))
      .lean<InstallationDocument>()
      .exec();
    return doc === null ? null : parse(doc.body);
  }

  async listForOrg(
    orgId: string,
    filter: InstallationFilter,
  ): Promise<Installation[]> {
    const query: QueryFilter<InstallationDocument> = { orgId };
    if (filter.status !== undefined) query.status = { $in: [...filter.status] };
    if (filter.kind !== undefined) query.kind = filter.kind;
    if (filter.owner !== undefined) query.owner = filter.owner;
    return this.find(query);
  }

  async create(tx: StoreTx, inst: Installation): Promise<void> {
    const row = {
      ...checkedObject(tx, installationSchema, inst, `installation ${inst.id}`),
      revision: 1,
    };
    await tx.enlist(async () => {
      try {
        await CpInstallationModel.create([toDocument(row)], {
          session: sessionOf(tx),
        });
      } catch (error) {
        if (isDuplicateKeyError(error)) {
          throw new ObjectExists(`installation ${row.id} exists`);
        }
        throw error;
      }
      await this.appendHistory(tx, row);
    });
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
    const _id = rowId(tx.orgId, row.id);
    await tx.enlist(async () => {
      const { matchedCount } = await CpInstallationModel.updateOne(
        { _id, revision: expectedRevision },
        { $set: toDocument(row) },
        { session: sessionOf(tx) },
      ).exec();
      if (matchedCount === 0) {
        const current = await CpInstallationModel.findById(_id, { revision: 1 })
          .session(sessionOf(tx) ?? null)
          .lean<Pick<InstallationDocument, 'revision'>>()
          .exec();
        if (current === null)
          throw new ObjectNotFound(`installation ${row.id}`);
        compareAndSet(
          current.revision,
          expectedRevision,
          `installation ${row.id}`,
        );
      }
      await this.appendHistory(tx, row);
    });
    return row.revision;
  }

  async history(
    orgId: string,
    id: string,
    limit: number,
  ): Promise<InstallationRevision[]> {
    const docs = await CpInstallationHistoryModel.find({ orgId, id })
      .sort({ revision: -1 })
      .limit(limit)
      .lean()
      .exec();
    return docs.map((doc) => ({
      revision: doc.revision,
      installation: parse(doc.body),
    }));
  }

  async children(orgId: string, parent: string): Promise<Installation[]> {
    return this.find({ orgId, installedVia: parent });
  }

  private async find(
    query: QueryFilter<InstallationDocument>,
  ): Promise<Installation[]> {
    const docs = await CpInstallationModel.find(query, { body: 1 })
      .sort({ _id: 1 })
      .lean<Array<Pick<InstallationDocument, 'body'>>>()
      .exec();
    return docs.map((doc) => parse(doc.body));
  }

  private async appendHistory(tx: StoreTx, row: Installation): Promise<void> {
    await CpInstallationHistoryModel.create(
      [
        {
          _id: `${rowId(row.orgId, row.id)}:${row.revision}`,
          orgId: row.orgId,
          id: row.id,
          revision: row.revision,
          body: JSON.stringify(row),
        },
      ],
      { session: sessionOf(tx) },
    );
  }
}
