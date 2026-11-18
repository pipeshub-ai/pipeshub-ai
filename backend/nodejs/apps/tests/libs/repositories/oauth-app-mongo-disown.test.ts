import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { MongoOAuthAppRepository } from '../../../src/libs/repositories/oauth-app/mongo';
import { OAuthApp } from '../../../src/modules/oauth_provider/schema/oauth.app.schema';
import { APP_OWNER_FORMAT, NO_APP_CREATOR } from '../../../src/libs/repositories/oauth-app';
import { FIRST_PARTY_DEVICE_CLIENT_ID } from '../../../src/modules/oauth_provider/constants/constants';

// The repair's MongoDB writes against an in-memory collection: each update applies to the matched
// rows at once (as MongoDB does per document), and an update that moves an org can be made to fail.
type Row = Record<string, unknown> & { _id: Types.ObjectId };

const same = (a: unknown, b: unknown): boolean => String(a) === String(b);

function matches(row: Row, filter: Record<string, any>): boolean {
  return Object.entries(filter).every(([key, want]) => {
    if (key === '$or') return (want as Record<string, any>[]).some((f) => matches(row, f));
    if (want === null) return row[key] === undefined || row[key] === null;
    if (want && typeof want === 'object' && '$in' in want) return want.$in.some((v: unknown) => same(row[key], v));
    return same(row[key], want);
  });
}

describe('MongoOAuthAppRepository.disownInstanceApps', () => {
  const org = new Types.ObjectId().toString();
  const shared = new Types.ObjectId().toString();
  const borrowed = new Types.ObjectId();
  let rows: Row[];
  let moveFails: boolean;

  beforeEach(() => {
    moveFails = false;
    rows = [
      { _id: new Types.ObjectId(), orgId: new Types.ObjectId(org), clientId: 'dcr', isDynamic: true, createdBy: borrowed },
      { _id: new Types.ObjectId(), orgId: new Types.ObjectId(org), clientId: FIRST_PARTY_DEVICE_CLIENT_ID, isDynamic: false, createdBy: borrowed },
    ];
    const pick = (filter: Record<string, any>) => rows.filter((r) => matches(r, filter));
    sinon.stub(OAuthApp, 'find').callsFake(((filter: Record<string, any>) => ({
      select: () => ({ lean: async () => pick(filter).map((r) => ({ _id: r._id, isDynamic: r.isDynamic })) }),
      lean: async () => pick(filter).map((r) => ({ _id: r._id, isDynamic: r.isDynamic })),
    })) as never);
    sinon.stub(OAuthApp, 'distinct').callsFake((async (_field: string, filter: Record<string, any>) =>
      pick(filter).map((r) => r._id)) as never);
    sinon.stub(OAuthApp, 'updateMany').callsFake((async (filter: Record<string, any>, update: { $set: Record<string, unknown> }) => {
      if (moveFails && 'orgId' in update.$set) throw new Error('write failed');
      const hit = pick(filter);
      for (const r of hit) Object.assign(r, update.$set);
      return { modifiedCount: hit.length };
    }) as never);
  });

  afterEach(() => sinon.restore());

  it('repairs a dynamic client on the next run when moving it failed', async () => {
    const repo = new MongoOAuthAppRepository();
    moveFails = true;
    await repo.disownInstanceApps(org, shared).catch(() => undefined);

    moveFails = false;
    await repo.disownInstanceApps(org, shared);

    const [dynamic, device] = rows;
    // The marker is written with the change it certifies, so a failed move leaves the client to repair.
    expect(String(dynamic!.orgId)).to.equal(shared);
    expect(dynamic).to.include({ ownerFormat: APP_OWNER_FORMAT });
    expect(String(dynamic!.createdBy)).to.equal(NO_APP_CREATOR);
    expect(String(device!.orgId)).to.equal(org);
    expect(device).to.include({ ownerFormat: APP_OWNER_FORMAT });
    expect(String(device!.createdBy)).to.equal(NO_APP_CREATOR);
    expect(await repo.disownInstanceApps(org, shared)).to.equal(0);
  });

  it('counts each app once', async () => {
    expect(await new MongoOAuthAppRepository().disownInstanceApps(org, shared)).to.equal(2);
  });
});
