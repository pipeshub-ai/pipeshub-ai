import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import { StorageController } from '../../../../src/modules/storage/controllers/storage.controller'
import { ConfigurationManagerServiceCommand } from '../../../../src/libs/commands/configuration_manager/cm.service.command'
import { TokenScopes } from '../../../../src/libs/enums/token-scopes.enum'

// The adapter needs the stored credentials whoever made the request. The
// user-facing config route answers {} on purpose so secrets never reach a
// browser; that answer must never become the config every caller shares.
describe('StorageController.getStorageConfig: credentials come from the server, whoever asks', () => {
  const SCOPED_SECRET = 'test-scoped-jwt-secret'
  const ORG_ID = 'org-1'
  const DEFAULT_CONFIG = { endpoint: 'http://localhost:3000' } as any
  const S3_CONFIG = {
    storageType: 's3',
    accessKeyId: 'AKIAEXAMPLE',
    secretAccessKey: 'stored-secret',
    region: 'us-east-1',
    bucketName: 'bucket',
  }

  let calls: Array<{ uri: string; authorization?: string }>
  let internalResponse: { statusCode: number; data: unknown }
  let controller: StorageController
  let kvs: any

  // The config is cached at module level; tests must not leak it.
  const clearConfigCache = () => kvs.watchKey.lastCall.args[1]()

  const userRequest = (): any => ({
    user: { orgId: ORG_ID, userId: 'user-1' },
    headers: { authorization: 'Bearer user-jwt' },
    params: {},
    query: {},
    body: {},
  })
  const serviceRequest = (): any => ({
    tokenPayload: { orgId: ORG_ID, scopes: [TokenScopes.STORAGE_TOKEN] },
    headers: { authorization: 'Bearer service-jwt' },
    params: {},
    query: {},
    body: {},
  })

  beforeEach(async () => {
    calls = []
    internalResponse = { statusCode: 200, data: S3_CONFIG }
    sinon
      .stub(ConfigurationManagerServiceCommand.prototype, 'execute')
      .callsFake(async function (this: any) {
        // The command lowercases header names.
        calls.push({ uri: this.uri, authorization: this.headers?.authorization })
        // As the CM routes answer: the internal route returns the stored
        // config, the user-facing one returns {}.
        return String(this.uri).endsWith('/internal/storageConfig')
          ? internalResponse
          : { statusCode: 200, data: {} }
      })
    kvs = {
      get: sinon.stub().resolves(JSON.stringify({ cm: { endpoint: 'http://cm:3000' } })),
      set: sinon.stub().resolves(),
      watchKey: sinon.stub().resolves(),
    }
    const logger: any = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() }
    controller = new StorageController(DEFAULT_CONFIG, logger, kvs, SCOPED_SECRET)
    await controller.watchStorageType(kvs)
    clearConfigCache()
  })

  afterEach(() => {
    clearConfigCache()
    sinon.restore()
  })

  it("gives a logged-in user's request the stored credentials, not the browser-safe {}", async () => {
    const config = await controller.getStorageConfig(userRequest(), kvs, DEFAULT_CONFIG)

    expect(config).to.deep.equal(S3_CONFIG)
  })

  it("still gives a service request the stored credentials after a user's request", async () => {
    await controller.getStorageConfig(userRequest(), kvs, DEFAULT_CONFIG)

    const config = await controller.getStorageConfig(serviceRequest(), kvs, DEFAULT_CONFIG)

    expect(config).to.deep.equal(S3_CONFIG)
  })

  it("asks the internal route with a storage token for the caller's org, never with the user's JWT", async () => {
    await controller.getStorageConfig(userRequest(), kvs, DEFAULT_CONFIG)

    expect(calls).to.have.lengthOf(1)
    expect(calls[0].uri).to.equal('http://cm:3000/api/v1/configurationManager/internal/storageConfig')
    const token = (calls[0].authorization ?? '').replace(/^Bearer /, '')
    expect(token).to.not.equal('user-jwt')
    const payload = jwt.verify(token, SCOPED_SECRET) as jwt.JwtPayload
    expect(payload.orgId).to.equal(ORG_ID)
    expect(payload.scopes).to.deep.equal([TokenScopes.STORAGE_TOKEN])
  })

  it('does not keep a failed response as the storage config', async () => {
    internalResponse = { statusCode: 400, data: { error: { message: 'Storage type not found' } } }
    let failed = false
    try {
      await controller.getStorageConfig(serviceRequest(), kvs, DEFAULT_CONFIG)
    } catch {
      failed = true
    }
    expect(failed, 'a failed config response must be an error, not a config').to.equal(true)

    internalResponse = { statusCode: 200, data: S3_CONFIG }
    const config = await controller.getStorageConfig(serviceRequest(), kvs, DEFAULT_CONFIG)

    expect(config).to.deep.equal(S3_CONFIG)
  })
})
