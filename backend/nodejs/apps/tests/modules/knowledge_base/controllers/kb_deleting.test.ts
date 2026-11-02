/**
 * A knowledge base that is being deleted, and a record that was pushed through the API.
 *
 *   - an upload to a KB whose status is DELETING is refused 409 KB_BEING_DELETED before any storage call
 *     (the role is checked first, so a caller with no write access learns nothing about the KB)
 *   - replacing the file of a pushed record (revision `pb<n>:...`) is refused 409 RECORD_IS_PUSHED before
 *     the storage upload; an ordinary record is not refused
 */

import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ConnectorServiceCommand } from '../../../../src/libs/commands/connector_service/connector.service.command'
import * as kbUtils from '../../../../src/modules/knowledge_base/utils/utils'
import {
  PUSHED_REVISION,
  updateRecord,
  uploadRecords,
} from '../../../../src/modules/knowledge_base/controllers/kb_controllers'
import { ForbiddenError, HttpError } from '../../../../src/libs/errors/http.errors'

const appConfig: any = {
  connectorBackend: 'http://localhost:8088',
  aiBackend: 'http://localhost:8000',
  storage: { endpoint: 'http://localhost:3003' },
  jwtSecret: 'test-jwt-secret',
  scopedJwtSecret: 'test-scoped-secret',
  cmBackend: 'http://localhost:3001',
}
const kvStore: any = { get: sinon.stub().resolves(null), set: sinon.stub().resolves(), delete: sinon.stub().resolves() }

function request(overrides: Record<string, any> = {}): any {
  return {
    headers: { authorization: 'Bearer test-token' },
    body: {},
    params: {},
    query: {},
    user: { userId: 'user-1', orgId: 'org-1', email: 'test@test.com', fullName: 'Test User' },
    context: { requestId: 'req-123' },
    socket: { setTimeout: sinon.stub() },
    on: sinon.stub(),
    ...overrides,
  }
}

function response(): any {
  const res: any = {}
  for (const name of ['status', 'json', 'end', 'send', 'setHeader', 'set', 'getHeader', 'pipe', 'writeHead', 'write', 'flush']) {
    res[name] = sinon.stub().returns(res)
  }
  res.headersSent = false
  return res
}

const SINGLE_FILE = [
  { originalname: 'a.pdf', mimetype: 'application/pdf', size: 10, filePath: 'a.pdf', lastModified: 1, buffer: Buffer.from('x') },
]

describe('upload to a knowledge base that is being deleted', () => {
  afterEach(() => sinon.restore())

  async function upload(kb: Record<string, any>) {
    const execute = sinon.stub(ConnectorServiceCommand.prototype, 'execute')
    execute.onFirstCall().resolves({ statusCode: 200, data: kb })
    execute.resolves({ statusCode: 500, data: {} })
    const placeholder = sinon.stub(kbUtils, 'createPlaceholderDocument')
    const next = sinon.stub()
    await uploadRecords(kvStore, appConfig)(
      request({ params: { kbId: 'kb-1' }, body: { fileBuffers: SINGLE_FILE } }),
      response(),
      next,
    )
    return { execute, placeholder, next }
  }

  it('is refused with 409 KB_BEING_DELETED before any storage or Python upload call', async () => {
    const { execute, placeholder, next } = await upload({ userRole: 'OWNER', status: 'DELETING' })

    const error = next.firstCall.args[0]
    expect(error).to.be.instanceOf(HttpError)
    expect(error.statusCode).to.equal(409)
    expect(error.code).to.equal('HTTP_KB_BEING_DELETED')
    expect(execute.callCount, 'only the KB read was made').to.equal(1)
    expect(placeholder.called).to.be.false
  })

  it('is refused for a writer as well', async () => {
    const { next } = await upload({ userRole: 'WRITER', status: 'DELETING' })
    expect(next.firstCall.args[0].code).to.equal('HTTP_KB_BEING_DELETED')
  })

  it('tells a caller without write access nothing about the state of the KB', async () => {
    const { next } = await upload({ userRole: 'READER', status: 'DELETING' })
    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })

  it('is not refused as being deleted when the KB is live (the control)', async () => {
    const { next } = await upload({ userRole: 'OWNER' })
    const error = next.firstCall?.args[0]
    expect(error?.code).to.not.equal('HTTP_KB_BEING_DELETED')
  })
})

describe('replacing the file of a pushed record', () => {
  afterEach(() => sinon.restore())

  const FILE = {
    fileBuffer: { originalname: 'report.pdf', mimetype: 'application/pdf', size: 4096, lastModified: 1, buffer: Buffer.from('new') },
  }

  async function replace(externalRevisionId: string | undefined) {
    const execute = sinon.stub(ConnectorServiceCommand.prototype, 'execute')
    execute.onFirstCall().resolves({
      statusCode: 200,
      data: { record: { externalRecordId: 'ext-doc-123', externalRevisionId }, knowledgeBase: { id: 'kb-1' } },
    })
    execute.onSecondCall().resolves({ statusCode: 200, data: { userRole: 'WRITER' } })
    execute.onThirdCall().resolves({ statusCode: 200, data: { updatedRecord: { _key: 'r1', recordName: 'report' } } })
    const upload = sinon.stub(kbUtils, 'uploadNextVersionToStorage').resolves()
    const next = sinon.stub()
    await updateRecord(kvStore, appConfig)(
      request({ params: { recordId: 'r1' }, body: { recordName: 'report', ...FILE } }),
      response(),
      next,
    )
    return { execute, upload, next }
  }

  it('refuses a pushed record with 409 RECORD_IS_PUSHED before the storage upload', async () => {
    const { execute, upload, next } = await replace('pb1:abc123:0123456789abcdef')

    const error = next.firstCall.args[0]
    expect(error.statusCode).to.equal(409)
    expect(error.code).to.equal('HTTP_RECORD_IS_PUSHED')
    expect(upload.called, 'no storage call').to.be.false
    expect(execute.callCount, 'the Python update was not made').to.equal(2)
  })

  it('does not refuse an ordinary record (the control)', async () => {
    const { upload, next } = await replace('v3')
    expect(next.called).to.be.false
    expect(upload.calledOnce).to.be.true
  })

  it('does not refuse a record whose revision only starts with pb', async () => {
    const { upload } = await replace('pbx:not-a-pushed-revision')
    expect(upload.calledOnce).to.be.true
  })

  it('matches the revision shape the Python service writes', () => {
    expect(PUSHED_REVISION.test('pb1:a:b')).to.be.true
    expect(PUSHED_REVISION.test('pb12:a')).to.be.true
    expect(PUSHED_REVISION.test('pb:a')).to.be.false
    expect(PUSHED_REVISION.test('xpb1:a')).to.be.false
  })
})
