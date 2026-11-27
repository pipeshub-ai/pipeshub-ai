import type { ClientSession } from 'mongoose';
import { isDuplicateKeyError } from '../../../utils/mongo.utils';
import {
  grantSchema,
  MAX_GRANT_ENTRIES,
  type Grant,
  type SubjectRef,
} from '../../../../modules/control-plane/domain/objects';
import { ObjectTooLarge } from '../../../../modules/control-plane/domain/store-errors';
import type { GrantStore } from '../../../../modules/control-plane/ports/grant-store';
import type { StoreTx } from '../../../../modules/control-plane/ports/store-tx';
import {
  checkedObject,
  compareAndSet,
} from '../../../../modules/control-plane/adapters/store-checks';
import {
  CpGrantModel,
  type GrantDocument,
} from '../installations/schema/installations.schema';

const rowId = (orgId: string, subject: string): string => `${orgId}:${subject}`;
const sessionOf = (tx: StoreTx): ClientSession | undefined =>
  (tx.session ?? undefined) as ClientSession | undefined;

const fromDocument = (doc: GrantDocument): Grant =>
  grantSchema.parse({
    schemaVersion: 1,
    orgId: doc.orgId,
    subject: doc.subject,
    entries: doc.entries,
    revision: doc.revision,
  });

/** `GrantStore` over `cpGrants`, one document per subject. */
export class MongoGrantStore implements GrantStore {
  async get(orgId: string, subject: SubjectRef): Promise<Grant | null> {
    const doc = await CpGrantModel.findById(rowId(orgId, subject))
      .lean<GrantDocument>()
      .exec();
    return doc === null ? null : fromDocument(doc);
  }

  async getMany(
    orgId: string,
    subjects: readonly SubjectRef[],
  ): Promise<Map<SubjectRef, Grant>> {
    const docs = await CpGrantModel.find({
      _id: { $in: subjects.map((subject) => rowId(orgId, subject)) },
    })
      .lean<GrantDocument[]>()
      .exec();
    return new Map(docs.map((doc) => [doc.subject, fromDocument(doc)]));
  }

  async put(
    tx: StoreTx,
    grant: Grant,
    expectedRevision: number,
  ): Promise<number> {
    if (grant.entries.length > MAX_GRANT_ENTRIES) {
      throw new ObjectTooLarge(
        `a grant holds at most ${MAX_GRANT_ENTRIES} entries, not ${grant.entries.length}`,
      );
    }
    const checked = checkedObject(
      tx,
      grantSchema,
      grant,
      `grant ${grant.subject}`,
    );
    const revision = expectedRevision + 1;
    const _id = rowId(tx.orgId, checked.subject);
    const doc: GrantDocument = {
      _id,
      orgId: tx.orgId,
      subject: checked.subject,
      entries: checked.entries,
      revision,
    };
    await tx.enlist(async () => {
      const session = sessionOf(tx);
      const current = async (): Promise<number> =>
        (
          await CpGrantModel.findById(_id, { revision: 1 })
            .session(session ?? null)
            .lean<Pick<GrantDocument, 'revision'>>()
            .exec()
        )?.revision ?? 0;
      const what = `grant ${checked.subject}`;
      compareAndSet(await current(), expectedRevision, what);
      // A writer that raced past the read: inside a transaction Mongo reports
      // a write conflict (retried by the transaction); outside one, this.
      if (expectedRevision === 0) {
        try {
          await CpGrantModel.create([doc], { session });
        } catch (error) {
          if (!isDuplicateKeyError(error)) throw error;
          compareAndSet(await current(), expectedRevision, what);
        }
        return;
      }
      const { matchedCount } = await CpGrantModel.updateOne(
        { _id, revision: expectedRevision },
        { $set: doc },
        { session },
      ).exec();
      if (matchedCount === 0)
        compareAndSet(await current(), expectedRevision, what);
    });
    return revision;
  }

  async usersOf(orgId: string, installation: string): Promise<SubjectRef[]> {
    const docs = await CpGrantModel.find(
      { orgId, 'entries.installation': installation },
      { subject: 1 },
    )
      .sort({ subject: 1 })
      .lean<Array<Pick<GrantDocument, 'subject'>>>()
      .exec();
    return docs.map((doc) => doc.subject);
  }
}
