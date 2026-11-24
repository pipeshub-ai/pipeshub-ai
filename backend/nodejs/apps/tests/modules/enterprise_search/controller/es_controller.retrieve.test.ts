import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { retrieve } from '../../../../src/modules/enterprise_search/controller/es_controller'
import EnterpriseSemanticSearch from '../../../../src/modules/enterprise_search/schema/search.schema'
import Citation from '../../../../src/modules/enterprise_search/schema/citation.schema'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { Logger } from '../../../../src/libs/services/logger.service'
import { SERVICE_UNAVAILABLE_MESSAGE } from '../../../../src/libs/errors/backend-error'
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

  describe('record page links', () => {
    const answer = async (frontendUrl: string | undefined, citation: Record<string, unknown>) => {
      sinon.restore()
      arm(200, { chunks: [{ id: 'v1:0', text: 't', citation }], incomplete: false })
      const res = createMockResponse()
      await retrieve({ ...appConfig, frontendUrl })(
        createMockRequest({ body: { query: 'q' }, user: { userId: 'u1', orgId: 'o1' } }),
        res,
        createMockNext(),
      )
      return res.json.firstCall.args[0].chunks[0].citation
    }

    it('turns a record page path into a full web app URL', async () => {
      const citation = await answer('https://pipeshub.example.com', {
        url: '/record/r1', recordUrl: '/record/r1', pageNum: null, blockIndex: 0,
      })
      expect(citation).to.deep.equal({
        url: 'https://pipeshub.example.com/record/r1',
        recordUrl: 'https://pipeshub.example.com/record/r1',
        pageNum: null,
        blockIndex: 0,
      })
    })

    it('keeps a web app served under a path, and a trailing slash adds none', async () => {
      const citation = await answer('https://example.com/pipeshub/', { url: '/record/r1', recordUrl: '/record/r1' })
      expect(citation.url).to.equal('https://example.com/pipeshub/record/r1')
    })

    it('leaves a source URL as it is', async () => {
      const url = 'https://acme.atlassian.net/browse/PP-1#:~:text=x'
      const citation = await answer('https://pipeshub.example.com', { url, recordUrl: 'https://acme.atlassian.net/browse/PP-1' })
      expect(citation.url).to.equal(url)
      expect(citation.recordUrl).to.equal('https://acme.atlassian.net/browse/PP-1')
    })

    it('never turns a protocol-relative link into one on the web app', async () => {
      const citation = await answer('https://pipeshub.example.com', { url: '//evil.example/x', recordUrl: null })
      expect(citation).to.deep.equal({ url: '//evil.example/x', recordUrl: null })
    })

    it('leaves the path when no web app URL is configured', async () => {
      for (const frontendUrl of [undefined, '', 'not a url']) {
        const citation = await answer(frontendUrl as string, { url: '/record/r1', recordUrl: '/record/r1' })
        expect(citation.url).to.equal('/record/r1')
      }
    })

    it('passes an answer without chunks on as it is', async () => {
      arm(200, { chunks: [], incomplete: false, message: 'none' })
      const res = createMockResponse()
      await retrieve({ ...appConfig, frontendUrl: 'https://x.test' })(
        createMockRequest({ body: { query: 'q' }, user: { userId: 'u1', orgId: 'o1' } }),
        res,
        createMockNext(),
      )
      expect(res.json.firstCall.args[0]).to.deep.include({ chunks: [], incomplete: false, message: 'none' })
    })
  })

  it('logs a 402 refusal as a warning, not an error', async () => {
    arm(402, { detail: { code: 'PAYMENT_REQUIRED', message: 'Payment required by the upstream service' } })
    const error = sinon.stub(Logger.prototype, 'error')
    const warn = sinon.stub(Logger.prototype, 'warn')
    const next = createMockNext()
    await retrieve(appConfig)(
      createMockRequest({ body: { query: 'q' }, user: { userId: 'u1', orgId: 'o1' } }),
      createMockResponse(),
      next,
    )

    expect(next.firstCall.args[0].statusCode).to.equal(402)
    expect(error.called).to.equal(false)
    expect(warn.calledWith('Error retrieving chunks')).to.equal(true)
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

  const failedCall = async (error: unknown) => {
    sinon.stub(Logger.prototype, 'error')
    sinon.stub(AIServiceCommand.prototype, 'execute').rejects(error)
    const next = createMockNext()
    await retrieve(appConfig)(
      createMockRequest({ body: { query: 'q' }, user: { userId: 'u1', orgId: 'o1' } }),
      createMockResponse(),
      next,
    )
    return next.firstCall.args[0]
  }

  it('answers 503 when the AI service is not listening, without the raw cause', async () => {
    const err = await failedCall(Object.assign(new TypeError('fetch failed'), { cause: { code: 'ECONNREFUSED' } }))

    expect(err.statusCode).to.equal(503)
    expect(err.message).to.equal(SERVICE_UNAVAILABLE_MESSAGE)
    expect(err.metadata).to.equal(undefined)
  })

  it('answers 504 when the AI service does not answer in time', async () => {
    const err = await failedCall(new DOMException('The operation was aborted due to timeout', 'TimeoutError'))

    expect(err.statusCode).to.equal(504)
  })

  it('answers 500 for any other failure of the call', async () => {
    const err = await failedCall(new Error('Unexpected end of JSON input'))

    expect(err.statusCode).to.equal(500)
  })
})
