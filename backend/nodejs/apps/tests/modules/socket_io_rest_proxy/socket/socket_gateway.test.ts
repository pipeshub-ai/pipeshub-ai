import { expect } from 'chai'
import sinon from 'sinon'
import { CliRpcSocketGateway } from '../../../../src/modules/socket_io_rest_proxy/socket/socket_gateway'

describe('CliRpcSocketGateway', () => {
  const getPort = () => 3001
  const makeGateway = () => {
    const authTokenService = {
      verifyToken: sinon.stub(),
    }
    return {
      gateway: new CliRpcSocketGateway(authTokenService as never, getPort),
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

  it('returns BAD_REQUEST for invalid rpc envelope', async () => {
    const { gateway } = makeGateway()
    const res = await (gateway as any).handleRequest(
      { type: 'bad', op: 'restProxy', id: '', payload: { method: 'GET', path: '/api/v1/connectors' } },
      makeSocket('Bearer token'),
    )
    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('BAD_REQUEST')
  })

  it('returns METHOD_NOT_ALLOWED when method is not whitelisted', async () => {
    const { gateway } = makeGateway()
    const res = await (gateway as any).handleRequest(
      { type: 'request', op: 'restProxy', id: '1', payload: { method: 'TRACE', path: '/api/v1/connectors' } },
      makeSocket('Bearer token'),
    )
    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('METHOD_NOT_ALLOWED')
  })

  it('returns UNAUTHORIZED when handshake token is missing', async () => {
    const { gateway } = makeGateway()
    const res = await (gateway as any).handleRequest(
      { type: 'request', op: 'restProxy', id: '2', payload: { method: 'GET', path: '/api/v1/connectors' } },
      makeSocket(undefined),
    )
    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('UNAUTHORIZED')
    expect(res.error.status).to.equal(401)
  })

  it('returns TOKEN_EXPIRED when token verification fails', async () => {
    const { gateway, verifyTokenStub } = makeGateway()
    verifyTokenStub.rejects(new Error('expired'))
    const res = await (gateway as any).handleRequest(
      { type: 'request', op: 'restProxy', id: '3', payload: { method: 'GET', path: '/api/v1/connectors' } },
      makeSocket('Bearer bad-token'),
    )
    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('TOKEN_EXPIRED')
    expect(res.error.status).to.equal(401)
  })

  it('returns PATH_NOT_ALLOWED when path fails allowlist', async () => {
    const { gateway, verifyTokenStub } = makeGateway()
    verifyTokenStub.resolves({ userId: 'u1', orgId: 'o1' })
    const res = await (gateway as any).handleRequest(
      { type: 'request', op: 'restProxy', id: '4', payload: { method: 'GET', path: '/internal/admin' } },
      makeSocket('Bearer token'),
    )
    expect(res.ok).to.equal(false)
    expect(res.error.code).to.equal('PATH_NOT_ALLOWED')
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
    expect(calledInit.headers).to.deep.equal({
      Authorization: 'Bearer token-123',
      Accept: 'application/json',
      'Content-Type': 'application/json',
    })
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
  })
})
