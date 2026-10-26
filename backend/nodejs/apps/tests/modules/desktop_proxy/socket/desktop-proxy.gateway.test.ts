import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { DesktopProxySocketGateway } from '../../../../src/modules/desktop_proxy/socket/desktop-proxy.gateway'

describe('DesktopProxySocketGateway', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('extractToken', () => {
    const makeGateway = () =>
      new DesktopProxySocketGateway({ verifyToken: sinon.stub() } as never)

    it('returns the token from a well-formed Bearer value', () => {
      expect((makeGateway() as any).extractToken('Bearer token-123')).to.equal('token-123')
    })

    it('returns null for a missing, bare, or empty-Bearer token', () => {
      const gateway = makeGateway() as any
      expect(gateway.extractToken('')).to.equal(null)
      expect(gateway.extractToken('not-a-bearer-token')).to.equal(null)
      expect(gateway.extractToken('Bearer ')).to.equal(null)
    })
  })

  describe('getHandshakeToken', () => {
    it('reads the handshake auth token and defaults to empty string', () => {
      const gateway = new DesktopProxySocketGateway(
        { verifyToken: sinon.stub() } as never,
      ) as any
      const socketWith = { handshake: { auth: { token: 'Bearer t' } } }
      const socketWithout = { handshake: { auth: {} } }

      expect(gateway.getHandshakeToken(socketWith)).to.equal('Bearer t')
      expect(gateway.getHandshakeToken(socketWithout)).to.equal('')
    })
  })
})

describe('DesktopProxySocketGateway.isLocalFsDeviceOnline', () => {
  it('returns null before the namespace is attached', () => {
    const authTokenService = { verifyToken: sinon.stub() }
    const gateway = new DesktopProxySocketGateway(authTokenService as never)
    expect(gateway.isReady()).to.equal(false)
    expect(gateway.isLocalFsDeviceOnline('org-1', 'user-1', 'dev-a')).to.equal(null)
    expect(gateway.isDesktopConnected('org-1', 'user-1')).to.equal(null)
  })
})

describe('DesktopProxySocketGateway session checks', () => {
  const USER = '64b000000000000000000001'
  const OTHER = '64b000000000000000000002'
  const ORG = '64b0000000000000000000aa'

  afterEach(() => {
    sinon.restore()
  })

  const makeSocket = (data: Record<string, unknown> = {}) => ({
    handshake: { auth: { token: 'Bearer t' } },
    data: { ...data },
    disconnect: sinon.stub(),
  })

  const makeGateway = (sessions: Record<string, sinon.SinonStub>) =>
    new DesktopProxySocketGateway(
      { verifyToken: sinon.stub().resolves({ userId: USER, orgId: ORG, role: 'member', iat: 1_000, email: 'old@x.com' }) } as never,
      sessions as never,
    ) as any

  describe('authenticateHandshake', () => {
    it('refuses a revoked session with a message the desktop client treats as auth', async () => {
      const gateway = makeGateway({
        findSessionRefusal: sinon.stub().resolves('User not found, please login again'),
      })
      const socket = makeSocket()

      const error = await gateway.authenticateHandshake(socket)

      expect(error).to.be.instanceOf(Error)
      expect(error.message).to.match(/auth/i)
      expect(error.message).to.contain('User not found')
      expect(socket.data.userId).to.equal(undefined)
    })

    it('accepts a live session and records the token issue time and email', async () => {
      const findSessionRefusal = sinon.stub().resolves(null)
      const gateway = makeGateway({ findSessionRefusal })
      const socket = makeSocket()

      expect(await gateway.authenticateHandshake(socket)).to.equal(undefined)
      expect(socket.data).to.deep.equal({
        userId: USER,
        orgId: ORG,
        issuedAt: 1_000,
        email: 'old@x.com',
      })
      expect(findSessionRefusal.firstCall.args[0]).to.include({ userId: USER, orgId: ORG })
    })

    // A user revoked before a database outage must not get in during it.
    it('refuses with a retryable, non-auth message when the session check fails', async () => {
      const gateway = makeGateway({
        findSessionRefusal: sinon.stub().rejects(new Error('mongo down')),
      })
      const socket = makeSocket()

      const error = await gateway.authenticateHandshake(socket)

      expect(error).to.be.instanceOf(Error)
      // The desktop client stops retrying on messages matching this pattern.
      expect(error.message).not.to.match(/token|auth|unauthor|expired/i)
      expect(socket.data.userId).to.equal(undefined)
    })
  })

  describe('disconnectRevokedSessions', () => {
    const withSockets = (gateway: any, sockets: ReturnType<typeof makeSocket>[]) => {
      gateway.namespace = { sockets: new Map(sockets.map((s, i) => [String(i), s])) }
      return gateway
    }

    it('disconnects only the sockets whose session no longer holds', async () => {
      // The token's email, not the account's current one, is what gets re-checked.
      const revoked = makeSocket({ userId: USER, orgId: ORG, issuedAt: 1_000, email: 'old@x.com' })
      const live = makeSocket({ userId: OTHER, orgId: ORG, issuedAt: 1_000 })
      const findSessionRefusals = sinon.stub().resolves(['This account is disabled', null])
      const gateway = withSockets(makeGateway({ findSessionRefusals }), [revoked, live])

      await gateway.disconnectRevokedSessions()

      expect(findSessionRefusals.firstCall.args[0]).to.deep.equal([
        { userId: USER, orgId: ORG, iat: 1_000, email: 'old@x.com' },
        { userId: OTHER, orgId: ORG, iat: 1_000, email: undefined },
      ])
      expect(revoked.disconnect.calledOnceWith(true)).to.equal(true)
      expect(live.disconnect.called).to.equal(false)
    })

    it('leaves every socket up when the re-check fails', async () => {
      const socket = makeSocket({ userId: USER, orgId: ORG, issuedAt: 1_000 })
      const gateway = withSockets(
        makeGateway({ findSessionRefusals: sinon.stub().rejects(new Error('mongo down')) }),
        [socket],
      )

      await gateway.disconnectRevokedSessions()

      expect(socket.disconnect.called).to.equal(false)
      expect(gateway.sessionRecheckRunning).to.equal(false)
    })
  })
})
