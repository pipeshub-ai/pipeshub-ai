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

describe('DesktopProxySocketGateway.isLocalFsDesktopOnline', () => {
  it('returns null before the namespace is attached', () => {
    const authTokenService = { verifyToken: sinon.stub() }
    const gateway = new DesktopProxySocketGateway(authTokenService as never)
    expect(gateway.isReady()).to.equal(false)
    expect(gateway.isLocalFsDesktopOnline('org-1', 'user-1', 'conn-1')).to.equal(null)
    expect(gateway.isDesktopConnected('org-1', 'user-1')).to.equal(null)
  })
})
