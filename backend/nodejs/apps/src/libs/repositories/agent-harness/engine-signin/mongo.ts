import { Users } from '../../../../modules/user_management/schema/users.schema';
import type { StoredEngineSignIn } from '../../../../modules/agent-harness/domain/engine-signin';
import type { EngineSignInStore } from '../../../../modules/agent-harness/ports/engine-signins';
import { EngineSignInModel } from './schema/engine-signin.schema';

const userKey = (orgId: string, userId: string): string =>
  `${orgId}:u:${userId}`;
const orgKey = (orgId: string): string => `${orgId}:org`;

export class MongoEngineSignIns implements EngineSignInStore {
  async read(
    orgId: string,
    userId: string,
  ): Promise<{ stored: StoredEngineSignIn; serviceAccount: boolean } | null> {
    const [user, doc] = await Promise.all([
      Users.findOne(
        { _id: userId, orgId, isDeleted: { $ne: true } },
        { kind: 1 },
      )
        .lean<{ kind?: string }>()
        .exec(),
      EngineSignInModel.findById(userKey(orgId, userId)).lean().exec(),
    ]);
    if (user === null) return null;
    const stored: StoredEngineSignIn = { enabled: doc?.enabled === true };
    if (doc?.email != null) stored.email = doc.email;
    if (doc?.plan != null) stored.plan = doc.plan;
    if (doc?.signedInAt != null) stored.signedInAt = doc.signedInAt;
    if (doc?.disconnectedAt != null) stored.disconnectedAt = doc.disconnectedAt;
    return { stored, serviceAccount: user.kind === 'service' };
  }

  async write(
    orgId: string,
    userId: string,
    next: StoredEngineSignIn,
  ): Promise<void> {
    // A replacement: a field the next state lacks (the email after a disconnect) is gone.
    await EngineSignInModel.replaceOne(
      { _id: userKey(orgId, userId) },
      { _id: userKey(orgId, userId), orgId, userId, ...next },
      { upsert: true },
    ).exec();
  }

  async signedInSince(orgId: string, since: number): Promise<string[]> {
    // shortcut: scans the org's records without an index (a few per person); add {orgId, signedInAt} if it grows.
    const docs = await EngineSignInModel.find(
      { orgId, enabled: true, signedInAt: { $gte: since } },
      { userId: 1 },
    )
      .lean<Array<{ userId?: string | null }>>()
      .exec();
    return docs
      .flatMap((d) => (typeof d.userId === 'string' ? [d.userId] : []))
      .sort();
  }

  async orgAllows(orgId: string): Promise<boolean> {
    const doc = await EngineSignInModel.findById(orgKey(orgId)).lean().exec();
    return doc?.claude === true;
  }

  async setOrgAllows(orgId: string, allowed: boolean): Promise<void> {
    await EngineSignInModel.replaceOne(
      { _id: orgKey(orgId) },
      { _id: orgKey(orgId), orgId, userId: null, claude: allowed },
      { upsert: true },
    ).exec();
  }

  async eraseUser(orgId: string, userId: string): Promise<void> {
    await EngineSignInModel.deleteOne({ _id: userKey(orgId, userId) }).exec();
  }

  async eraseOrg(orgId: string): Promise<void> {
    // shortcut: no index on orgId (a few documents per org, erasure only); add one if the collection grows.
    await EngineSignInModel.deleteMany({ orgId }).exec();
  }
}
