import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { forkArtifactCopier } from '../../../../src/modules/enterprise_search/controller/es_controller'

const appConfig = { connectorBackend: 'http://connectors.test' } as never
const req = { headers: { authorization: 'Bearer user-token', host: 'ignored', 'content-length': '0' } } as never

const reply = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

const refs = (n: number) => Array.from({ length: n }, (_, i) => ({ artifactId: `a${i}`, upToVersion: 1 }))

describe('forkArtifactCopier', () => {
  let fetchStub: sinon.SinonStub

  beforeEach(() => {
    fetchStub = sinon.stub(globalThis, 'fetch')
  })
  afterEach(() => {
    sinon.restore()
  })

  it('posts the refs to the connectors service as the caller, in one attempt', async () => {
    fetchStub.resolves(reply(200, { copies: [{ sourceArtifactId: 'a0', artifactId: 'n0', versions: [] }], failed: [] }))

    const out = await forkArtifactCopier(appConfig, req)('fork-1', refs(1))

    expect(out.copies.map((c) => c.artifactId)).to.deep.equal(['n0'])
    expect(fetchStub.calledOnce).to.equal(true)
    const [url, init] = fetchStub.firstCall.args as [string, RequestInit]
    expect(url).to.equal('http://connectors.test/api/v1/artifacts/copy')
    expect(init.method).to.equal('POST')
    expect(JSON.parse(String(init.body))).to.deep.equal({ conversationId: 'fork-1', artifacts: refs(1) })
    const headers = init.headers as Record<string, string>
    expect(headers.authorization).to.equal('Bearer user-token')
    expect(headers).to.not.have.property('content-length')
  })

  it('splits a long list into batches and joins the answers', async () => {
    fetchStub.callsFake(async (_url: string, init: RequestInit) => {
      const sent = JSON.parse(String(init.body)).artifacts as { artifactId: string }[]
      return reply(200, {
        copies: sent.map((s) => ({ sourceArtifactId: s.artifactId, artifactId: `n-${s.artifactId}`, versions: [] })),
        failed: [{ artifactId: `x-${sent[0]!.artifactId}`, reason: 'not_found' }],
      })
    })

    const out = await forkArtifactCopier(appConfig, req)('fork-1', refs(120))

    expect(fetchStub.callCount).to.equal(3)
    expect(out.copies).to.have.length(120)
    expect(out.failed.map((f) => f.artifactId)).to.deep.equal(['x-a0', 'x-a50', 'x-a100'])
  })

  it('does not repeat a request that failed, so no second copy is left behind', async () => {
    fetchStub.rejects(new Error('connection reset'))

    try {
      await forkArtifactCopier(appConfig, req)('fork-1', refs(1))
      expect.fail('expected the copy to fail')
    } catch (error) {
      expect((error as Error).message).to.equal('connection reset')
    }
    expect(fetchStub.calledOnce).to.equal(true)
  })

  it('turns an error status from the connectors service into an error', async () => {
    fetchStub.resolves(reply(500, { detail: 'boom' }))

    try {
      await forkArtifactCopier(appConfig, req)('fork-1', refs(1))
      expect.fail('expected the copy to fail')
    } catch (error) {
      expect((error as { statusCode?: number }).statusCode).to.be.at.least(500)
    }
  })
})
