import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { FixedClock } from '../../../../../../src/libs/types/clock'
import {
  AGENT_READINESS_TTL_MS,
  HttpAgentReadinessAdapter,
} from '../../../../../../src/modules/enterprise_search/services/collaboration/readiness/http-agent-readiness.adapter'

const subject = { orgId: 'o1', userId: 'u1' }

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

describe('HttpAgentReadinessAdapter', () => {
  let fetchStub: sinon.SinonStub
  let issue: sinon.SinonStub
  let warn: sinon.SinonStub
  let clock: FixedClock
  let adapter: HttpAgentReadinessAdapter

  beforeEach(() => {
    fetchStub = sinon.stub(globalThis, 'fetch')
    issue = sinon.stub().returns('svc-token')
    warn = sinon.stub()
    clock = new FixedClock(1_000_000)
    adapter = new HttpAgentReadinessAdapter(() => 'http://ai', { issue }, { warn }, clock)
  })
  afterEach(() => sinon.restore())

  it('CL-08: reports the missing and unauthenticated toolsets as blocked', async () => {
    fetchStub.resolves(jsonResponse(200, { canSend: false, missingToolsets: ['gmail'], unauthenticatedToolsets: ['slack'] }))
    expect(await adapter.check(subject, 'agent 1')).to.deep.equal({ status: 'blocked', toolsets: ['gmail', 'slack'] })
    const [url, init] = fetchStub.firstCall.args
    expect(String(url)).to.equal('http://ai/api/v1/agent/agent%201/readiness')
    expect(init.method).to.equal('GET')
    expect(new Headers(init.headers).get('authorization')).to.equal('Bearer svc-token')
    expect(issue.firstCall.args[0]).to.deep.include({ userId: 'u1', orgId: 'o1' })
  })

  it('reports ready when Python says canSend', async () => {
    fetchStub.resolves(jsonResponse(200, { canSend: true, missingToolsets: [], unauthenticatedToolsets: [] }))
    expect(await adapter.check(subject, 'a')).to.deep.equal({ status: 'ready' })
  })

  it('CL-09: a second call inside 60 s does not call Python; after the TTL it does', async () => {
    fetchStub.callsFake(async () => jsonResponse(200, { canSend: true, missingToolsets: [], unauthenticatedToolsets: [] }))
    await adapter.check(subject, 'a')
    clock.advance(AGENT_READINESS_TTL_MS - 1)
    await adapter.check(subject, 'a')
    expect(fetchStub.callCount).to.equal(1)
    clock.advance(1)
    await adapter.check(subject, 'a')
    expect(fetchStub.callCount).to.equal(2)
  })

  it('keys the cache by org, user and agent', async () => {
    fetchStub.callsFake(async () => jsonResponse(200, { canSend: true, missingToolsets: [], unauthenticatedToolsets: [] }))
    await adapter.check(subject, 'a')
    await adapter.check({ ...subject, userId: 'u2' }, 'a')
    await adapter.check({ ...subject, orgId: 'o2' }, 'a')
    await adapter.check(subject, 'b')
    expect(fetchStub.callCount).to.equal(4)
  })

  it('invalidate drops the cached answer', async () => {
    fetchStub.callsFake(async () => jsonResponse(200, { canSend: false, missingToolsets: ['x'], unauthenticatedToolsets: [] }))
    await adapter.check(subject, 'a')
    adapter.invalidate(subject, 'a')
    await adapter.check(subject, 'a')
    expect(fetchStub.callCount).to.equal(2)
  })

  it('CL-09: a transport failure is unknown, never a deny, and is not cached', async () => {
    fetchStub.rejects(new Error('ECONNREFUSED'))
    expect(await adapter.check(subject, 'a')).to.deep.equal({ status: 'unknown' })
    expect(warn.calledOnce).to.equal(true)
    fetchStub.resolves(jsonResponse(200, { canSend: true, missingToolsets: [], unauthenticatedToolsets: [] }))
    expect(await adapter.check(subject, 'a')).to.deep.equal({ status: 'ready' })
  })

  it('a token minting failure is unknown', async () => {
    issue.throws(new Error('no secret'))
    expect(await adapter.check(subject, 'a')).to.deep.equal({ status: 'unknown' })
    expect(fetchStub.called).to.equal(false)
  })

  it('treats a 404 answer as an agent that is not available, and caches it', async () => {
    fetchStub.resolves(jsonResponse(404, { detail: 'Agent not found' }))
    expect(await adapter.check(subject, 'a')).to.deep.equal({ status: 'unavailable' })
    await adapter.check(subject, 'a')
    expect(fetchStub.callCount).to.equal(1)
  })

  for (const [name, response] of [
    ['500', () => jsonResponse(500, { detail: 'boom' })],
    ['malformed body', () => jsonResponse(200, { canSend: 'yes' })],
    ['missing arrays', () => jsonResponse(200, { canSend: false })],
  ] as const) {
    it(`treats a ${name} answer as unknown`, async () => {
      fetchStub.resolves(response())
      expect(await adapter.check(subject, 'a')).to.deep.equal({ status: 'unknown' })
    })
  }
})
