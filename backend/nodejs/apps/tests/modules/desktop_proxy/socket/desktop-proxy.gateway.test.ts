import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { DesktopProxySocketGateway } from '../../../../src/modules/desktop_proxy/socket/desktop-proxy.gateway'

describe('DesktopProxySocketGateway', () => {
  const getPort = () => 3001
  const makeGateway = () => {
    const authTokenService = {
      verifyToken: sinon.stub(),
    }
    return {
      gateway: new DesktopProxySocketGateway(authTokenService as never, getPort),
      verifyTokenStub: authTokenService.verifyToken,
    }
  }

  const makeSocket = (token?: unknown) =>
    ({
      handshake: { auth: { token } },
      data: {},
      emit: sinon.stub(),
    }) as never

  afterEach(() => {
    sinon.restore()
  })

  it('returns BAD_REQUEST when request id is empty (invalid RPC envelope)', async () => {
    const { gateway } = makeGateway()
    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: '',
        payload: { method: 'GET', path: '/api/v1/connectors' },
      },
      makeSocket('Bearer token'),
    )
    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('BAD_REQUEST')
  })

  it('returns BAD_REQUEST when request id is blank after trim', async () => {
    const { gateway } = makeGateway()
    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: '   ',
        payload: { method: 'GET', path: '/api/v1/connectors' },
      },
      makeSocket('Bearer token'),
    )
    expect(res.ok).to.equal(false)
    expect(res.id).to.equal('unknown')
    expect(res.error.code).to.equal('BAD_REQUEST')
  })

  it('returns METHOD_NOT_ALLOWED when method is not whitelisted', async () => {
    const { gateway } = makeGateway()
    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: '1',
        payload: { method: 'TRACE', path: '/api/v1/connectors' },
      },
      makeSocket('Bearer token'),
    )
    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('METHOD_NOT_ALLOWED')
  })

  it('defaults blank method to GET', async () => {
    const { gateway, verifyTokenStub } = makeGateway()
    verifyTokenStub.resolves({ userId: 'u1', orgId: 'o1' })
    const fetchStub = sinon.stub(globalThis as any, 'fetch').resolves({
      status: 200,
      text: async () => '{}',
    } as Response)

    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: 'm1',
        payload: { method: '   ', path: '/api/v1/connectors' },
      },
      makeSocket('Bearer token'),
    )
    expect(res.ok).to.equal(true)
    const [, calledInit] = fetchStub.firstCall.args as [string, RequestInit]
    expect(calledInit.method).to.equal('GET')
  })

  it('returns UNAUTHORIZED when handshake token is missing', async () => {
    const { gateway } = makeGateway()
    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: '2',
        payload: { method: 'GET', path: '/api/v1/connectors' },
      },
      makeSocket(undefined),
    )
    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('UNAUTHORIZED')
    expect(res.error.message).to.equal('Authentication token missing')
    expect(res.error.status).to.equal(401)
  })

  it('returns UNAUTHORIZED when handshake value is not Bearer token', async () => {
    const { gateway } = makeGateway()
    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: '2b',
        payload: { method: 'GET', path: '/api/v1/connectors' },
      },
      makeSocket('not-a-bearer-token'),
    )
    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('UNAUTHORIZED')
  })

  it('returns UNAUTHORIZED when Bearer scheme has empty token', async () => {
    const { gateway } = makeGateway()
    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: '2c',
        payload: { method: 'GET', path: '/api/v1/connectors' },
      },
      makeSocket('Bearer '),
    )
    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('UNAUTHORIZED')
  })

  it('returns TOKEN_EXPIRED when token verification fails', async () => {
    const { gateway, verifyTokenStub } = makeGateway()
    verifyTokenStub.rejects(new Error('expired'))
    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: '3',
        payload: { method: 'GET', path: '/api/v1/connectors' },
      },
      makeSocket('Bearer bad-token'),
    )
    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('TOKEN_EXPIRED')
    expect(res.error.message).to.equal('Authentication token expired or invalid')
    expect(res.error.status).to.equal(401)
  })

  it('returns PATH_NOT_ALLOWED when path fails allowlist', async () => {
    const { gateway, verifyTokenStub } = makeGateway()
    verifyTokenStub.resolves({ userId: 'u1', orgId: 'o1' })
    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: '4',
        payload: { method: 'GET', path: '/internal/admin' },
      },
      makeSocket('Bearer token'),
    )
    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('PATH_NOT_ALLOWED')
    expect(res.error.message).to.equal('Path is not allowed for REST proxy')
  })

  it('proxies request and parses json response body', async () => {
    const { gateway, verifyTokenStub } = makeGateway()
    verifyTokenStub.resolves({ userId: 'u1', orgId: 'o1' })

    const fetchStub = sinon.stub(globalThis as any, 'fetch').resolves({
      status: 201,
      text: async () => '{"ok":true,"count":2}',
    } as Response)

    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: '5',
        payload: {
          method: 'POST',
          path: '/api/v1/connectors',
          query: { page: 2, active: true, skip: null },
          body: { hello: 'world' },
        },
      },
      makeSocket('Bearer token-123'),
    )

    expect(res.ok).to.equal(true)
    expect(res.result.status).to.equal(201)
    expect(res.result.body).to.deep.equal({ ok: true, count: 2 })
    expect(fetchStub.calledOnce).to.equal(true)
    const [calledUrl, calledInit] = fetchStub.firstCall.args as [string, RequestInit]
    expect(calledUrl).to.contain('http://127.0.0.1:3001/api/v1/connectors')
    expect(calledUrl).to.contain('page=2')
    expect(calledUrl).to.contain('active=true')
    expect(calledUrl).to.not.contain('skip=')
    const headers = calledInit.headers as Record<string, string>
    expect(headers.Authorization).to.equal('Bearer token-123')
    expect(headers.Accept).to.equal('application/json')
    expect(headers['Content-Type']).to.equal('application/json')
    expect(calledInit.body).to.equal(JSON.stringify({ hello: 'world' }))
  })

  it('allows knowledgeBase and crawlingManager allowlisted roots', async () => {
    const { gateway, verifyTokenStub } = makeGateway()
    verifyTokenStub.resolves({ userId: 'u1', orgId: 'o1' })
    const fetchStub = sinon.stub(globalThis as any, 'fetch').resolves({
      status: 200,
      text: async () => '{"x":1}',
    } as Response)

    const kb = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: 'kb1',
        payload: { method: 'GET', path: '/api/v1/knowledgeBase/items' },
      },
      makeSocket('Bearer t'),
    )
    expect(kb.ok).to.equal(true)
    expect(fetchStub.firstCall.args[0] as string).to.match(/\/api\/v1\/knowledgeBase\/items$/)

    const cm = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: 'cm1',
        payload: { method: 'GET', path: '/api/v1/crawlingManager/jobs' },
      },
      makeSocket('Bearer t'),
    )
    expect(cm.ok).to.equal(true)
    expect(fetchStub.secondCall.args[0] as string).to.match(/\/api\/v1\/crawlingManager\/jobs$/)
  })

  it('returns text body when upstream payload is not json', async () => {
    const { gateway, verifyTokenStub } = makeGateway()
    verifyTokenStub.resolves({ userId: 'u1', orgId: 'o1' })
    sinon.stub(globalThis as any, 'fetch').resolves({
      status: 200,
      text: async () => 'plain-text',
    } as Response)

    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: '6',
        payload: { method: 'GET', path: '/api/v1/connectors' },
      },
      makeSocket('Bearer token'),
    )

    expect(res.ok).to.equal(true)
    expect(res.result.body).to.equal('plain-text')
  })

  it('returns null body when upstream response is empty whitespace', async () => {
    const { gateway, verifyTokenStub } = makeGateway()
    verifyTokenStub.resolves({ userId: 'u1', orgId: 'o1' })
    sinon.stub(globalThis as any, 'fetch').resolves({
      status: 204,
      text: async () => '  \n  ',
    } as Response)

    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: '6b',
        payload: { method: 'GET', path: '/api/v1/connectors' },
      },
      makeSocket('Bearer token'),
    )

    expect(res.ok).to.equal(true)
    expect(res.result.body).to.equal(null)
  })

  it('returns UPSTREAM_ERROR when fetch throws', async () => {
    const { gateway, verifyTokenStub } = makeGateway()
    verifyTokenStub.resolves({ userId: 'u1', orgId: 'o1' })
    sinon.stub(globalThis as any, 'fetch').rejects(new Error('network down'))

    const res = await (gateway as any).handleRequest(
      {
        type: 'request',
        op: 'restProxy',
        id: '7',
        payload: { method: 'GET', path: '/api/v1/connectors' },
      },
      makeSocket('Bearer token'),
    )

    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('UPSTREAM_ERROR')
    expect(res.error.message).to.equal('network down')
  })
})
