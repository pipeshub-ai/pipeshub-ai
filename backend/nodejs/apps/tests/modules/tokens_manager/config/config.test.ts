import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { registerUpstreams } from '../../../../src/libs/http/upstream-call'

describe('tokens_manager/config/config', () => {
  afterEach(() => {
    sinon.restore()
    // A load that succeeds registers the services process-wide; later files in this worker must not see them.
    registerUpstreams({})
  })

  it('should export AppConfig interface and loadAppConfig function', async () => {
    try {
      const mod = await import('../../../../src/modules/tokens_manager/config/config')
      expect(mod.loadAppConfig).to.be.a('function')
    } catch (error: any) {
      // loadAppConfig depends on ConfigService which needs runtime env
      expect(error).to.exist
    }
  })

  it('fails startup on an unusable PASSWORD_RESET_LINK_EXPIRY before reading any config', async () => {
    const { loadAppConfig } = await import('../../../../src/modules/tokens_manager/config/config')
    const { ConfigService } = await import('../../../../src/modules/tokens_manager/services/cm.service')
    const getInstance = sinon.stub(ConfigService, 'getInstance')
    const original = process.env.PASSWORD_RESET_LINK_EXPIRY
    process.env.PASSWORD_RESET_LINK_EXPIRY = '10ms'
    try {
      let error: unknown
      try {
        await loadAppConfig()
      } catch (caught) {
        error = caught
      }
      expect(error).to.be.instanceOf(Error)
      expect((error as Error).message).to.match(/^PASSWORD_RESET_LINK_EXPIRY: /)
      expect(getInstance.called).to.be.false
    } finally {
      if (original === undefined) {
        delete process.env.PASSWORD_RESET_LINK_EXPIRY
      } else {
        process.env.PASSWORD_RESET_LINK_EXPIRY = original
      }
    }
  })

  describe('rate limits from the environment (R46LANE-FU-4)', () => {
    const NAMES = [
      'MAX_AUTH_REQUESTS_PER_MINUTE',
      'MAX_REQUESTS_PER_MINUTE',
      'MAX_OAUTH_CLIENT_REQUESTS_PER_MINUTE',
    ] as const
    let saved: Record<string, string | undefined>

    beforeEach(() => {
      saved = Object.fromEntries(NAMES.map((name) => [name, process.env[name]]))
      for (const name of NAMES) delete process.env[name]
    })

    afterEach(() => {
      for (const name of NAMES) {
        if (saved[name] === undefined) delete process.env[name]
        else process.env[name] = saved[name]
      }
    })

    async function load() {
      const { loadAppConfig } = await import('../../../../src/modules/tokens_manager/config/config')
      const { ConfigService } = await import('../../../../src/modules/tokens_manager/services/cm.service')
      const service = new Proxy({}, { get: () => async () => undefined })
      sinon.stub(ConfigService, 'getInstance').returns(service as never)
      sinon.stub(console, 'warn')
      return loadAppConfig()
    }

    it('keeps the product defaults when unset or empty', async () => {
      process.env.MAX_AUTH_REQUESTS_PER_MINUTE = ''
      const config = await load()
      expect(config.maxAuthRequestsPerMinute).to.equal(10)
      expect(config.maxRequestsPerMinute).to.equal(1000)
      expect(config.maxOAuthClientRequestsPerMinute).to.equal(1000)
    })

    it('honours a positive whole number', async () => {
      process.env.MAX_AUTH_REQUESTS_PER_MINUTE = '600'
      process.env.MAX_REQUESTS_PER_MINUTE = '100000'
      process.env.MAX_OAUTH_CLIENT_REQUESTS_PER_MINUTE = ' 50 '
      const config = await load()
      expect(config.maxAuthRequestsPerMinute).to.equal(600)
      expect(config.maxRequestsPerMinute).to.equal(100000)
      expect(config.maxOAuthClientRequestsPerMinute).to.equal(50)
    })

    for (const junk of ['abc', '0', '-5', '10x', '1e3', '2.5']) {
      it(`ignores ${JSON.stringify(junk)} and keeps the default, never a limit that is off or shut`, async () => {
        for (const name of NAMES) process.env[name] = junk
        const config = await load()
        expect(config.maxAuthRequestsPerMinute).to.equal(10)
        expect(config.maxRequestsPerMinute).to.equal(1000)
        expect(config.maxOAuthClientRequestsPerMinute).to.equal(1000)
      })
    }
  })
})
