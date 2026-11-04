/**
 * Mongo OAuth app / token repositories and IdentityLookup against a real MongoDB
 * (standalone and replica set).
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0 REPLICA_SET_AVAILABLE=true
 *   MONGO_IT_URI=mongodb://localhost:27017                 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
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
