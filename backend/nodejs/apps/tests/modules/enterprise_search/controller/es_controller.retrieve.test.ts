import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { retrieve } from '../../../../src/modules/enterprise_search/controller/es_controller'
import EnterpriseSemanticSearch from '../../../../src/modules/enterprise_search/schema/search.schema'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { createMockRequest, createMockResponse, createMockNext } from '../../../helpers/mock-request'

// POST /api/v1/retrieve is called by developers' apps on every request they
// serve, so what matters is that it forwards the query untouched and, unlike
// search, stores nothing.

describe('retrieve controller', () => {
  const appConfig: any = { aiBackend: 'http://ai.test' }
  let seen: { uri?: string; body?: any }

  const arm = (statusCode: number, data: Record<string, unknown>) => {
    seen = {}
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (this: any) {
      seen = { uri: this.uri, body: JSON.parse(this.body) }
      return Promise.resolve({ statusCode, data } as any)
    })
  }

  afterEach(() => sinon.restore())

  it('forwards only the fields the caller set to the AI service retrieve route', async () => {
    arm(200, { chunks: [], incomplete: false })
    const req = createMockRequest({
      body: { query: 'rollback', limit: 5, filters: { kb: ['kb1'] }, modifiedAfter: '2026-01-01' },
      user: { userId: 'u1', orgId: 'o1' },
    })
    await retrieve(appConfig)(req, createMockResponse(), createMockNext())

    expect(seen.uri).to.equal('http://ai.test/api/v1/retrieve')
    expect(seen.body).to.deep.equal({
      query: 'rollback', limit: 5, filters: { kb: ['kb1'] }, modifiedAfter: '2026-01-01',
    })
  })

  it('answers with the chunks and stores no citation or search history', async () => {
    const chunks = [{ id: 'v1:0', text: 'Roll back the canary.' }]
    arm(200, { chunks, incomplete: false })
    const insertMany = sinon.stub(Citation, 'insertMany')
    const save = sinon.stub(EnterpriseSemanticSearch.prototype, 'save')
    const req = createMockRequest({ body: { query: 'rollback' }, user: { userId: 'u1', orgId: 'o1' } })
    req.context = { requestId: 'req-1' }
    const res = createMockResponse()
    const next = createMockNext()

    await retrieve(appConfig)(req, res, next)

    expect(next.called).to.be.false
    expect(res.status.calledWith(200)).to.be.true
    expect(res.json.firstCall.args[0]).to.deep.equal({ requestId: 'req-1', chunks, incomplete: false })
    expect(insertMany.called).to.be.false
    expect(save.called).to.be.false
  })

  it('passes an AI service failure on as an error with its status', async () => {
    arm(503, { message: 'index unreachable' })
    const next = createMockNext()
    await retrieve(appConfig)(
      createMockRequest({ body: { query: 'q' }, user: { userId: 'u1', orgId: 'o1' } }),
      createMockResponse(),
      next,
    )

    expect(next.calledOnce).to.be.true
    const err = next.firstCall.args[0]
    expect(err.statusCode ?? err.status).to.equal(503)
  })
})
