import 'reflect-metadata'
import { expect } from 'chai'
import { DesktopProxyContainer } from '../../../../src/modules/desktop_proxy/container/desktop-proxy.container'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { DesktopProxySocketGateway } from '../../../../src/modules/desktop_proxy/socket/desktop-proxy.gateway'

describe('DesktopProxyContainer', () => {
  const appConfig = {
    jwtSecret: 'test-jwt-secret',
    scopedJwtSecret: 'test-scoped-jwt-secret',
  } as any

  afterEach(() => {
    DesktopProxyContainer.dispose()
    ;(DesktopProxyContainer as any).container = null
  })

  describe('initialize', () => {
    it('returns an inversify Container with required bindings', async () => {
      const container = await DesktopProxyContainer.initialize(appConfig)

      expect(container).to.exist
      expect(container.isBound(AuthTokenService)).to.equal(true)
      expect(container.isBound(DesktopProxySocketGateway)).to.equal(true)
    })

    it('binds AuthTokenService as a constant value reused across resolutions', async () => {
      const container = await DesktopProxyContainer.initialize(appConfig)

      const auth1 = container.get(AuthTokenService)
      const auth2 = container.get(AuthTokenService)

      expect(auth1).to.be.instanceOf(AuthTokenService)
      expect(auth1).to.equal(auth2)
    })

    it('binds DesktopProxySocketGateway in singleton scope', async () => {
      const container = await DesktopProxyContainer.initialize(appConfig)

      const gateway1 = container.get(DesktopProxySocketGateway)
      const gateway2 = container.get(DesktopProxySocketGateway)

      expect(gateway1).to.be.instanceOf(DesktopProxySocketGateway)
      expect(gateway1).to.equal(gateway2)
    })

    it('exposes the container via the static field after initialize', async () => {
      const container = await DesktopProxyContainer.initialize(appConfig)

      expect((DesktopProxyContainer as any).container).to.equal(container)
    })

    it('returns an isolated container per call', async () => {
      const containerA = await DesktopProxyContainer.initialize(appConfig)
      const containerB = await DesktopProxyContainer.initialize(appConfig)

      expect(containerA).to.not.equal(containerB)
      expect((DesktopProxyContainer as any).container).to.equal(containerB)
    })
  })

  describe('dispose', () => {
    it('unbinds all bindings on the active container', async () => {
      const container = await DesktopProxyContainer.initialize(appConfig)

      DesktopProxyContainer.dispose()

      expect(container.isBound(AuthTokenService)).to.equal(false)
      expect(container.isBound(DesktopProxySocketGateway)).to.equal(false)
    })

    it('is a no-op when called before initialize', () => {
      ;(DesktopProxyContainer as any).container = null

      expect(() => DesktopProxyContainer.dispose()).to.not.throw()
    })
  })
})
