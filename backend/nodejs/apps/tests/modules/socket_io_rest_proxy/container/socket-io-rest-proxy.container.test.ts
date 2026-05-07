import 'reflect-metadata'
import { expect } from 'chai'
import { SocketIoRestProxyContainer } from '../../../../src/modules/socket_io_rest_proxy/container/socket-io-rest-proxy.container'
import { AuthTokenService } from '../../../../src/libs/services/authtoken.service'
import { CliRpcSocketGateway } from '../../../../src/modules/socket_io_rest_proxy/socket/socket_gateway'

describe('SocketIoRestProxyContainer', () => {
  const appConfig = {
    jwtSecret: 'test-jwt-secret',
    scopedJwtSecret: 'test-scoped-jwt-secret',
  } as any

  afterEach(() => {
    SocketIoRestProxyContainer.dispose()
    ;(SocketIoRestProxyContainer as any).container = null
  })

  describe('initialize', () => {
    it('returns an inversify Container with required bindings', async () => {
      const container = await SocketIoRestProxyContainer.initialize(appConfig, () => 3001)

      expect(container).to.exist
      expect(container.isBound(AuthTokenService)).to.equal(true)
      expect(container.isBound(CliRpcSocketGateway)).to.equal(true)
    })

    it('binds AuthTokenService as a constant value reused across resolutions', async () => {
      const container = await SocketIoRestProxyContainer.initialize(appConfig, () => 3001)

      const auth1 = container.get(AuthTokenService)
      const auth2 = container.get(AuthTokenService)

      expect(auth1).to.be.instanceOf(AuthTokenService)
      expect(auth1).to.equal(auth2)
    })

    it('binds CliRpcSocketGateway in singleton scope', async () => {
      const container = await SocketIoRestProxyContainer.initialize(appConfig, () => 3001)

      const gateway1 = container.get(CliRpcSocketGateway)
      const gateway2 = container.get(CliRpcSocketGateway)

      expect(gateway1).to.be.instanceOf(CliRpcSocketGateway)
      expect(gateway1).to.equal(gateway2)
    })

    it('passes the getPort resolver to the gateway so it reads the live port', async () => {
      let currentPort = 4000
      const getPort = () => currentPort

      const container = await SocketIoRestProxyContainer.initialize(appConfig, getPort)
      const gateway = container.get(CliRpcSocketGateway) as any

      expect(gateway.getPort()).to.equal(4000)
      currentPort = 4100
      expect(gateway.getPort()).to.equal(4100)
    })

    it('exposes the container via the static field after initialize', async () => {
      const container = await SocketIoRestProxyContainer.initialize(appConfig, () => 3001)

      expect((SocketIoRestProxyContainer as any).container).to.equal(container)
    })

    it('returns an isolated container per call', async () => {
      const containerA = await SocketIoRestProxyContainer.initialize(appConfig, () => 3001)
      const containerB = await SocketIoRestProxyContainer.initialize(appConfig, () => 4001)

      expect(containerA).to.not.equal(containerB)
      expect((SocketIoRestProxyContainer as any).container).to.equal(containerB)
    })
  })

  describe('dispose', () => {
    it('unbinds all bindings on the active container', async () => {
      const container = await SocketIoRestProxyContainer.initialize(appConfig, () => 3001)

      SocketIoRestProxyContainer.dispose()

      expect(container.isBound(AuthTokenService)).to.equal(false)
      expect(container.isBound(CliRpcSocketGateway)).to.equal(false)
    })

    it('is a no-op when called before initialize', () => {
      ;(SocketIoRestProxyContainer as any).container = null

      expect(() => SocketIoRestProxyContainer.dispose()).to.not.throw()
    })
  })
})
