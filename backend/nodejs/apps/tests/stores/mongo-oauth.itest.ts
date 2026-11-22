/**
 * Mongo OAuth app / token repositories and IdentityLookup against a real MongoDB
 * (standalone and replica set).
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0 REPLICA_SET_AVAILABLE=true
 *   MONGO_IT_URI=mongodb://localhost:27017                 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import mongoose from 'mongoose';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoOAuthAppRepository } from '../../src/libs/repositories/oauth-app/mongo';
import { MongoOAuthTokenRepository } from '../../src/libs/repositories/oauth-token/mongo';
import { MongoIdentityLookup } from '../../src/libs/repositories/identity-lookup/mongo';
import { OAuthApp } from '../../src/modules/oauth_provider/schema/oauth.app.schema';
import { OAuthAccessToken } from '../../src/modules/oauth_provider/schema/oauth.access_token.schema';
import { OAuthRefreshToken } from '../../src/modules/oauth_provider/schema/oauth.refresh_token.schema';
import { AuthorizationCode } from '../../src/modules/oauth_provider/schema/authorization_code.schema';
import { OAuthDeviceCode } from '../../src/modules/oauth_provider/schema/oauth.device_code.schema';
import { Org } from '../../src/modules/user_management/schema/org.schema';
import { describeOAuthAppContract } from './contract/oauth-app.contract';
import { NO_APP_CREATOR, OAuthGrantType } from '../../src/libs/repositories/oauth-app';
import { FIRST_PARTY_DEVICE_CLIENT_ID } from '../../src/modules/oauth_provider/constants/constants';
import { describeOAuthTokenContract } from './contract/oauth-token.contract';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set for the MongoDB tests`);
  return value;
}

describe('OAuth repositories against a real MongoDB', function () {
  this.timeout(60000);
  const uri = requireEnv('MONGO_IT_URI');
  const rsFlag = requireEnv('REPLICA_SET_AVAILABLE');
  if (rsFlag !== 'true' && rsFlag !== 'false') {
    throw new Error(`REPLICA_SET_AVAILABLE must be true or false, got '${rsFlag}'`);
  }
  const dbName = `pipeshub_it_${randomUUID().slice(0, 8)}`;
  let mongo: MongoService;
  const oid = (id: string) => new mongoose.Types.ObjectId(id);

  before(async () => {
    mongo = new MongoService({ uri, db: dbName });
    await mongo.initialize();
    // Unique indexes matter to these tests. The codes and refresh/access tokens
    // keep their TTL index off so expired rows stay for the assertions.
    for (const m of [OAuthApp, OAuthAccessToken, OAuthRefreshToken, OAuthDeviceCode, AuthorizationCode, Org]) {
      await m.createCollection();
    }
    await OAuthApp.syncIndexes();
    await OAuthAccessToken.collection.createIndex({ tokenHash: 1 }, { unique: true });
    await OAuthRefreshToken.collection.createIndex({ tokenHash: 1 }, { unique: true });
    await OAuthDeviceCode.collection.createIndex({ userCode: 1 }, { unique: true });
  });

  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  const apps = new MongoOAuthAppRepository();
  const newId = () => String(new mongoose.Types.ObjectId());

  describe('OAuthAppRepository', () => {
    describeOAuthAppContract({
      repo: apps,
      newId,
      rawKeys: async (id) => Object.keys((await OAuthApp.findById(id).lean()) as object),
      setCreatedAt: async (id, at) => {
        await OAuthApp.collection.updateOne({ _id: oid(id) }, { $set: { createdAt: at } });
      },
      clearOwnerFormat: async (id) => {
        await OAuthApp.collection.updateOne({ _id: oid(id) }, { $unset: { ownerFormat: '' } });
      },
    });
  });

  describe('OAuthAppRepository on MongoDB only', () => {
    it('moves the dynamic clients of an org to another one, and leaves the device app in place', async () => {
      await OAuthApp.deleteMany({ clientId: FIRST_PARTY_DEVICE_CLIENT_ID });
      const org = newId();
      const shared = newId();
      const base = {
        clientSecretEncrypted: 'enc',
        name: 'App',
        orgId: org,
        createdBy: newId(),
        redirectUris: ['https://a.example/cb'],
        allowedGrantTypes: [OAuthGrantType.AUTHORIZATION_CODE],
        allowedScopes: ['org:read'],
        isConfidential: false,
      };
      const dynamic = await apps.create({ ...base, clientId: `cid-${newId()}`, isDynamic: true });
      const device = await apps.create({ ...base, clientId: FIRST_PARTY_DEVICE_CLIENT_ID });

      for (const a of [dynamic, device]) {
        await OAuthApp.collection.updateOne({ _id: oid(a._id) }, { $unset: { ownerFormat: '' } });
      }
      // Each app once, though the dynamic one needed both writes (as PostgreSQL counts).
      expect(await apps.disownInstanceApps(org, shared)).to.equal(2);

      expect(await apps.findByClientId(dynamic.clientId)).to.include({ orgId: shared, createdBy: NO_APP_CREATOR });
      expect(await apps.findByClientId(device.clientId)).to.include({ orgId: org, createdBy: NO_APP_CREATOR });
      expect(await apps.disownInstanceApps(org, shared)).to.equal(0);
      await OAuthApp.deleteOne({ clientId: FIRST_PARTY_DEVICE_CLIENT_ID });
    });
  });

  describe('OAuthTokenRepository and IdentityLookup', () => {
    describeOAuthTokenContract({
      tokens: new MongoOAuthTokenRepository(),
      lookup: new MongoIdentityLookup(),
      apps,
      newId,
      rawAccessKeys: async (id) => Object.keys((await OAuthAccessToken.findById(id).lean()) as object),
      rawAccess: async (id) => (await OAuthAccessToken.findById(id).lean()) as Record<string, unknown> | null,
      rawRefresh: async (id) => (await OAuthRefreshToken.findById(id).lean()) as Record<string, unknown> | null,
      rawDevice: async (id) => (await OAuthDeviceCode.findById(id).lean()) as Record<string, unknown> | null,
      setAccessCreatedAt: async (id, at) => {
        await OAuthAccessToken.collection.updateOne({ _id: oid(id) }, { $set: { createdAt: at } });
      },
    });
  });
});
