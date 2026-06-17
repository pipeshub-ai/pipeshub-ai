/**
 * Tests for the KB/folder existence + write-permission pre-checks added to
 * uploadRecordsToKB and uploadRecordsToFolder (Blockers 1–2 fix).
 *
 * Critical invariants tested:
 *   - 404 from the KB endpoint → NotFoundError BEFORE any storage interaction
 *   - 403 from the KB endpoint → ForbiddenError BEFORE any storage interaction
 *   - Non-200/404/403 from the KB endpoint → InternalServerError
 *   - 200 with userRole = READER → ForbiddenError (Blocker 2: read-only users blocked)
 *   - 200 with userRole = COMMENTER → ForbiddenError
 *   - 200 with userRole = undefined/null → ForbiddenError
 *   - 200 with userRole = OWNER → pre-check passes
 *   - 200 with userRole = WRITER → pre-check passes
 *   - Folder upload: same KB checks + additional folder 404/403/500 checks
 *   - createPlaceholderDocument must NOT be called on any failure path
 */

import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ConnectorServiceCommand } from '../../../../src/libs/commands/connector_service/connector.service.command'
import * as kbUtils from '../../../../src/modules/knowledge_base/utils/utils'
import {
  uploadRecordsToKB,
  uploadRecordsToFolder,
} from '../../../../src/modules/knowledge_base/controllers/kb_controllers'
import {
  ForbiddenError,
  InternalServerError,
  NotFoundError,
} from '../../../../src/libs/errors/http.errors'

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function createMockRequest(overrides: Record<string, any> = {}): any {
  return {
    headers: { authorization: 'Bearer test-token' },
    body: {},
    params: {},
    query: {},
    user: { userId: 'user-1', orgId: 'org-1', email: 'test@test.com', fullName: 'Test User' },
    context: { requestId: 'req-123' },
    // Streaming upload clears the per-response socket timeout and listens for close.
    socket: { setTimeout: sinon.stub() },
    on: sinon.stub(),
    ...overrides,
  }
}

function createMockResponse(): any {
  const res: any = {
    status: sinon.stub(),
    json: sinon.stub(),
    end: sinon.stub(),
    send: sinon.stub(),
    setHeader: sinon.stub(),
    set: sinon.stub(),
    getHeader: sinon.stub(),
    headersSent: false,
    pipe: sinon.stub(),
    // Streaming upload (SSE) response surface.
    writeHead: sinon.stub(),
    write: sinon.stub(),
    flush: sinon.stub(),
  }
  res.status.returns(res)
  res.json.returns(res)
  res.end.returns(res)
  res.send.returns(res)
  res.writeHead.returns(res)
  return res
}

function createMockNext(): sinon.SinonStub {
  return sinon.stub()
}

function createMockAppConfig(): any {
  return {
    connectorBackend: 'http://localhost:8088',
    aiBackend: 'http://localhost:8000',
    storage: { endpoint: 'http://localhost:3003' },
    jwtSecret: 'test-jwt-secret',
    scopedJwtSecret: 'test-scoped-secret',
    cmBackend: 'http://localhost:3001',
  }
}

function createMockKeyValueStore(): any {
  return {
    get: sinon.stub().resolves(null),
    set: sinon.stub().resolves(),
    delete: sinon.stub().resolves(),
  }
}

/** One valid file buffer — enough to pass the early validation guard. */
const SINGLE_FILE = [
  {
    originalname: 'test.pdf',
    mimetype: 'application/pdf',
    size: 1024,
    filePath: 'test.pdf',
    lastModified: Date.now(),
    buffer: Buffer.from('fake content'),
  },
]

/**
 * Stub ConnectorServiceCommand.execute() for the first n calls.
 * Each entry in `responses` is returned in order.
 */
function stubConnectorCalls(responses: Array<{ statusCode: number; data?: any }>) {
  const stub = sinon.stub(ConnectorServiceCommand.prototype, 'execute')
  responses.forEach((r, i) => {
    stub.onCall(i).resolves(r)
  })
  // Default for any unexpected extra calls
  stub.resolves({ statusCode: 200, data: { userRole: 'OWNER' } })
  return stub
}

// ---------------------------------------------------------------------------
// uploadRecordsToKB — KB pre-check tests
// ---------------------------------------------------------------------------

describe('uploadRecordsToKB — KB existence and write-permission pre-check', () => {
  afterEach(() => {
    sinon.restore()
  })

  // -- 404 path ------------------------------------------------------------

  it('throws NotFoundError when KB endpoint returns 404', async () => {
    stubConnectorCalls([{ statusCode: 404 }])

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-missing' },
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError)
  })

  it('does NOT call createPlaceholderDocument when KB returns 404', async () => {
    stubConnectorCalls([{ statusCode: 404 }])
    const placeholderStub = sinon.stub(kbUtils, 'createPlaceholderDocument')

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-missing' },
      body: { fileBuffers: SINGLE_FILE },
    })

    await handler(req, createMockResponse(), createMockNext())

    expect(placeholderStub.called).to.be.false
  })

  // -- 403 path (no access at all) -----------------------------------------

  it('throws ForbiddenError when KB endpoint returns 403', async () => {
    stubConnectorCalls([{ statusCode: 403 }])

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-1' },
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })

  // -- Unexpected status codes ---------------------------------------------

  it('throws InternalServerError when KB endpoint returns 500', async () => {
    stubConnectorCalls([{ statusCode: 500 }])

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-1' },
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(InternalServerError)
  })

  it('throws InternalServerError when KB endpoint returns 503', async () => {
    stubConnectorCalls([{ statusCode: 503 }])

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-1' },
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(InternalServerError)
  })

  // -- Role-based write-permission check (Blocker 2) ----------------------

  it('throws ForbiddenError when userRole is READER (200 but read-only)', async () => {
    stubConnectorCalls([{ statusCode: 200, data: { userRole: 'READER' } }])

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-1' },
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })

  it('does NOT call createPlaceholderDocument when userRole is READER', async () => {
    stubConnectorCalls([{ statusCode: 200, data: { userRole: 'READER' } }])
    const placeholderStub = sinon.stub(kbUtils, 'createPlaceholderDocument')

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-1' },
      body: { fileBuffers: SINGLE_FILE },
    })

    await handler(req, createMockResponse(), createMockNext())

    expect(placeholderStub.called).to.be.false
  })

  it('throws ForbiddenError when userRole is COMMENTER', async () => {
    stubConnectorCalls([{ statusCode: 200, data: { userRole: 'COMMENTER' } }])

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-1' },
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })

  it('throws ForbiddenError when userRole is absent from response body', async () => {
    stubConnectorCalls([{ statusCode: 200, data: {} }])

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-1' },
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })

  it('throws ForbiddenError when response data is null', async () => {
    stubConnectorCalls([{ statusCode: 200, data: null }])

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-1' },
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })

  it('throws ForbiddenError when userRole is ORGANIZER', async () => {
    // ORGANIZER is a valid role but not in the write-permitted set [OWNER, WRITER]
    stubConnectorCalls([{ statusCode: 200, data: { userRole: 'ORGANIZER' } }])

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-1' },
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })

  // -- Allowed roles -------------------------------------------------------

  it('proceeds past pre-check when userRole is OWNER', async () => {
    stubConnectorCalls([{ statusCode: 200, data: { userRole: 'OWNER' } }])
    sinon.stub(kbUtils, 'createPlaceholderDocument').resolves({
      documentId: 'doc-1',
      documentName: 'test',
    })
    sinon.stub(kbUtils, 'processUploadsInBackground').resolves()

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-1' },
      body: { fileBuffers: SINGLE_FILE },
    })
    const res = createMockResponse()
    const next = createMockNext()

    await handler(req, res, next)

    // If next was not called with an error, the pre-check passed
    const errorCallArgs = next.args.find((a: any[]) => a[0] instanceof Error)
    expect(errorCallArgs).to.be.undefined
  })

  it('proceeds past pre-check when userRole is WRITER', async () => {
    stubConnectorCalls([{ statusCode: 200, data: { userRole: 'WRITER' } }])
    sinon.stub(kbUtils, 'createPlaceholderDocument').resolves({
      documentId: 'doc-1',
      documentName: 'test',
    })
    sinon.stub(kbUtils, 'processUploadsInBackground').resolves()

    const handler = uploadRecordsToKB(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: { kbId: 'kb-1' },
      body: { fileBuffers: SINGLE_FILE },
    })
    const res = createMockResponse()
    const next = createMockNext()

    await handler(req, res, next)

    const errorCallArgs = next.args.find((a: any[]) => a[0] instanceof Error)
    expect(errorCallArgs).to.be.undefined
  })
})

// ---------------------------------------------------------------------------
// uploadRecordsToFolder — KB + folder pre-check tests
// ---------------------------------------------------------------------------

describe('uploadRecordsToFolder — KB and folder pre-check', () => {
  afterEach(() => {
    sinon.restore()
  })

  const REQ_PARAMS = { kbId: 'kb-1', folderId: 'folder-1' }

  // -- KB checks (same as uploadRecordsToKB) -------------------------------

  it('throws NotFoundError when KB endpoint returns 404', async () => {
    stubConnectorCalls([{ statusCode: 404 }])

    const handler = uploadRecordsToFolder(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: REQ_PARAMS,
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError)
  })

  it('throws ForbiddenError when KB endpoint returns 403', async () => {
    stubConnectorCalls([{ statusCode: 403 }])

    const handler = uploadRecordsToFolder(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: REQ_PARAMS,
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })

  it('throws InternalServerError when KB endpoint returns non-200/404/403', async () => {
    stubConnectorCalls([{ statusCode: 502 }])

    const handler = uploadRecordsToFolder(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: REQ_PARAMS,
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.firstCall.args[0]).to.be.instanceOf(InternalServerError)
  })

  it('throws ForbiddenError when KB returns 200 but userRole is READER', async () => {
    stubConnectorCalls([{ statusCode: 200, data: { userRole: 'READER' } }])

    const handler = uploadRecordsToFolder(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: REQ_PARAMS,
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })

  it('does NOT call createPlaceholderDocument when KB role is READER', async () => {
    stubConnectorCalls([{ statusCode: 200, data: { userRole: 'READER' } }])
    const placeholderStub = sinon.stub(kbUtils, 'createPlaceholderDocument')

    const handler = uploadRecordsToFolder(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: REQ_PARAMS,
      body: { fileBuffers: SINGLE_FILE },
    })

    await handler(req, createMockResponse(), createMockNext())

    expect(placeholderStub.called).to.be.false
  })

  // -- Folder checks -------------------------------------------------------

  it('throws NotFoundError when folder endpoint returns 404 (KB check passed)', async () => {
    // First call (KB check) → 200 + OWNER; second call (folder check) → 404
    stubConnectorCalls([
      { statusCode: 200, data: { userRole: 'OWNER' } },
      { statusCode: 404 },
    ])

    const handler = uploadRecordsToFolder(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: REQ_PARAMS,
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(NotFoundError)
  })

  it('does NOT call createPlaceholderDocument when folder returns 404', async () => {
    stubConnectorCalls([
      { statusCode: 200, data: { userRole: 'OWNER' } },
      { statusCode: 404 },
    ])
    const placeholderStub = sinon.stub(kbUtils, 'createPlaceholderDocument')

    const handler = uploadRecordsToFolder(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: REQ_PARAMS,
      body: { fileBuffers: SINGLE_FILE },
    })

    await handler(req, createMockResponse(), createMockNext())

    expect(placeholderStub.called).to.be.false
  })

  it('throws ForbiddenError when folder endpoint returns 403', async () => {
    stubConnectorCalls([
      { statusCode: 200, data: { userRole: 'OWNER' } },
      { statusCode: 403 },
    ])

    const handler = uploadRecordsToFolder(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: REQ_PARAMS,
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })

  it('throws InternalServerError when folder endpoint returns non-200/404/403', async () => {
    stubConnectorCalls([
      { statusCode: 200, data: { userRole: 'WRITER' } },
      { statusCode: 500 },
    ])

    const handler = uploadRecordsToFolder(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: REQ_PARAMS,
      body: { fileBuffers: SINGLE_FILE },
    })
    const next = createMockNext()

    await handler(req, createMockResponse(), next)

    expect(next.firstCall.args[0]).to.be.instanceOf(InternalServerError)
  })

  it('proceeds to upload when KB = OWNER and folder check passes', async () => {
    stubConnectorCalls([
      { statusCode: 200, data: { userRole: 'OWNER' } },
      { statusCode: 200, data: {} },
    ])
    sinon.stub(kbUtils, 'createPlaceholderDocument').resolves({
      documentId: 'doc-1',
      documentName: 'test',
    })
    sinon.stub(kbUtils, 'processUploadsInBackground').resolves()

    const handler = uploadRecordsToFolder(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: REQ_PARAMS,
      body: { fileBuffers: SINGLE_FILE },
    })
    const res = createMockResponse()
    const next = createMockNext()

    await handler(req, res, next)

    const errorCallArgs = next.args.find((a: any[]) => a[0] instanceof Error)
    expect(errorCallArgs).to.be.undefined
  })

  it('proceeds to upload when KB = WRITER and folder check passes', async () => {
    stubConnectorCalls([
      { statusCode: 200, data: { userRole: 'WRITER' } },
      { statusCode: 200, data: {} },
    ])
    sinon.stub(kbUtils, 'createPlaceholderDocument').resolves({
      documentId: 'doc-1',
      documentName: 'test',
    })
    sinon.stub(kbUtils, 'processUploadsInBackground').resolves()

    const handler = uploadRecordsToFolder(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: REQ_PARAMS,
      body: { fileBuffers: SINGLE_FILE },
    })
    const res = createMockResponse()
    const next = createMockNext()

    await handler(req, res, next)

    const errorCallArgs = next.args.find((a: any[]) => a[0] instanceof Error)
    expect(errorCallArgs).to.be.undefined
  })

  // -- Edge: READER on KB, folder check should never be reached -----------

  it('folder check is NOT called when KB role check fails (READER)', async () => {
    const connectorStub = stubConnectorCalls([
      { statusCode: 200, data: { userRole: 'READER' } },
    ])

    const handler = uploadRecordsToFolder(createMockKeyValueStore(), createMockAppConfig())
    const req = createMockRequest({
      params: REQ_PARAMS,
      body: { fileBuffers: SINGLE_FILE },
    })

    await handler(req, createMockResponse(), createMockNext())

    // Only 1 connector call should have been made (the KB check)
    expect(connectorStub.callCount).to.equal(1)
  })
})
