import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import type { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service'
import * as configurationUtil from '../../../src/modules/configuration_manager/utils/util'
import * as localFsUtils from '../../../src/utils/local-fs-utils'
import { KB_UPLOAD_LIMITS } from '../../../src/modules/knowledge_base/constants/kb.constants'
import { createLocalFsConnectorFileEventsUploadMiddleware } from '../../../src/libs/middlewares/local-fs.middleware'

function createMockRequest(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    headers: { 'content-type': 'multipart/form-data' },
    body: {},
    params: {},
    ...overrides,
  }
}

function createMockResponse(): Record<string, unknown> {
  return {
    status: sinon.stub().returnsThis(),
    json: sinon.stub().returnsThis(),
  }
}

describe('local-fs.middleware', () => {
  const keyValueStoreService = {} as KeyValueStoreService

  afterEach(() => {
    sinon.restore()
  })

  it('createLocalFsConnectorFileEventsUploadMiddleware returns an Express handler', () => {
    sinon.stub(configurationUtil, 'getPlatformSettingsFromStore').resolves({
      fileUploadMaxSizeBytes: 1024,
      featureFlags: {},
    })
    sinon.stub(localFsUtils, 'createLocalFsConnectorUploadMulter').returns({
      any: () => sinon.stub().callsFake((_req: unknown, _res: unknown, next: (err?: unknown) => void) => next()),
    } as ReturnType<typeof localFsUtils.createLocalFsConnectorUploadMulter>)

    const handler = createLocalFsConnectorFileEventsUploadMiddleware(keyValueStoreService)
    expect(handler).to.be.a('function')
  })

  it('uses fileUploadMaxSizeBytes from platform settings and KB max files for multer limits', async () => {
    const platformBytes = 64 * 1024 * 1024
    const getSettingsStub = sinon.stub(configurationUtil, 'getPlatformSettingsFromStore').resolves({
      fileUploadMaxSizeBytes: platformBytes,
      featureFlags: {},
    })

    const multerAny = sinon.stub().callsFake((_req: unknown, _res: unknown, next: (err?: unknown) => void) =>
      next(),
    )
    const createMulterStub = sinon.stub(localFsUtils, 'createLocalFsConnectorUploadMulter').callsFake((limits) => {
      expect(limits.maxFileSizeBytes).to.equal(platformBytes)
      expect(limits.maxFiles).to.equal(KB_UPLOAD_LIMITS.maxFilesPerRequest)
      return { any: () => multerAny } as ReturnType<typeof localFsUtils.createLocalFsConnectorUploadMulter>
    })

    const handler = createLocalFsConnectorFileEventsUploadMiddleware(keyValueStoreService)
    const req = createMockRequest()
    const res = createMockResponse()

    await new Promise<void>((resolve, reject) => {
      const next = sinon.stub().callsFake((err?: unknown) => {
        if (err) reject(err)
        else resolve()
      })
      handler(req as never, res as never, next as never)
    })

    expect(getSettingsStub.calledOnce).to.be.true
    expect(getSettingsStub.firstCall.args[0]).to.equal(keyValueStoreService)
    expect(createMulterStub.calledOnce).to.be.true
    expect(multerAny.calledOnce).to.be.true
  })

  it('falls back to KB_UPLOAD_LIMITS.defaultMaxFileSizeBytes when platform settings fail', async () => {
    sinon.stub(configurationUtil, 'getPlatformSettingsFromStore').rejects(new Error('kv unavailable'))

    const multerAny = sinon.stub().callsFake((_req: unknown, _res: unknown, next: (err?: unknown) => void) =>
      next(),
    )
    sinon.stub(localFsUtils, 'createLocalFsConnectorUploadMulter').callsFake((limits) => {
      expect(limits.maxFileSizeBytes).to.equal(KB_UPLOAD_LIMITS.defaultMaxFileSizeBytes)
      expect(limits.maxFiles).to.equal(KB_UPLOAD_LIMITS.maxFilesPerRequest)
      return { any: () => multerAny } as ReturnType<typeof localFsUtils.createLocalFsConnectorUploadMulter>
    })

    const handler = createLocalFsConnectorFileEventsUploadMiddleware(keyValueStoreService)
    const req = createMockRequest()
    const res = createMockResponse()

    let outerNext!: sinon.SinonStub
    await new Promise<void>((resolve, reject) => {
      outerNext = sinon.stub().callsFake((err?: unknown) => {
        if (err) reject(err)
        else resolve()
      })
      handler(req as never, res as never, outerNext as never)
    })

    expect(outerNext.calledOnce).to.be.true
    expect(outerNext.firstCall.args[0]).to.be.undefined
  })

  it('calls next(err) when multer any() forwards an error', async () => {
    sinon.stub(configurationUtil, 'getPlatformSettingsFromStore').resolves({
      fileUploadMaxSizeBytes: 1024,
      featureFlags: {},
    })

    const multerErr = new Error('LIMIT_FILE_SIZE')
    const multerAny = sinon.stub().callsFake((_req: unknown, _res: unknown, next: (err?: unknown) => void) =>
      next(multerErr),
    )
    sinon.stub(localFsUtils, 'createLocalFsConnectorUploadMulter').returns({
      any: () => multerAny,
    } as ReturnType<typeof localFsUtils.createLocalFsConnectorUploadMulter>)

    const handler = createLocalFsConnectorFileEventsUploadMiddleware(keyValueStoreService)
    const req = createMockRequest()
    const res = createMockResponse()
    const next = sinon.stub()

    await new Promise<void>((resolve) => {
      handler(req as never, res as never, (err?: unknown) => {
        next(err)
        resolve()
      })
    })

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.equal(multerErr)
  })
})
