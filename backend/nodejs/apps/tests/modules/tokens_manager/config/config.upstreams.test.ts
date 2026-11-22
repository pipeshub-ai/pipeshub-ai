import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { loadAppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { ConfigService } from '../../../../src/modules/tokens_manager/services/cm.service'
import { registerUpstreams, upstreamFetch, upstreamTarget } from '../../../../src/libs/http/upstream-call'
import { InternalServerError } from '../../../../src/libs/errors/http.errors'

const URLS: Record<string, string> = {
  getAiBackendUrl: 'http://query:8000',
  getConnectorUrl: 'http://connector:8088',
  getIndexingUrl: 'http://indexing:8091',
  getIamBackendUrl: 'http://gateway:3000/api/v1/users',
  getCmBackendUrl: 'http://gateway:3000',
  getStorageBackendUrl: 'http://storage:3001',
}

// The app wires the registry in loadAppConfig, so every load (and a reload) names the services it calls.
describe('loadAppConfig registers the PipesHub services for upstream.call.end', () => {
  afterEach(() => {
    sinon.restore()
    registerUpstreams({})
  })

  it('names the query, connector, indexing and Node services; anything else stays external', async () => {
    const service = new Proxy({}, { get: (_target, name) => async () => URLS[String(name)] ?? 'http://unused:1' })
    sinon.stub(ConfigService, 'getInstance').returns(service as unknown as ConfigService)

    const appConfig = await loadAppConfig()

    expect(upstreamTarget(`${appConfig.aiBackend}/api/v1/chat`).upstream).to.equal('query')
    expect(upstreamTarget(`${appConfig.connectorBackend}/api/v1/stats`).upstream).to.equal('connector')
    expect(upstreamTarget(`${appConfig.indexingBackend}/health`).upstream).to.equal('indexing')
    for (const url of [appConfig.iamBackend, appConfig.cmBackend, appConfig.storageBackend]) {
      expect(upstreamTarget(`${url}/x`).upstream, url).to.equal('nodejs')
    }
    expect(upstreamTarget('https://oauth2.googleapis.com/token').upstream).to.equal('external')
  })

  it('keeps the bases of an earlier load: after a reload moves a service, the old URL is still sent to, an unregistered one is not', async () => {
    const urls = { ...URLS }
    const service = new Proxy({}, { get: (_target, name) => async () => urls[String(name)] ?? 'http://unused:1' })
    sinon.stub(ConfigService, 'getInstance').returns(service as unknown as ConfigService)
    const fetchStub = sinon.stub(globalThis, 'fetch').resolves(new Response(null, { status: 204 }))

    await loadAppConfig()
    urls.getAiBackendUrl = 'http://query-2:8000'
    await loadAppConfig()

    await upstreamFetch('http://query:8000/api/v1/chat', { method: 'GET' })
    await upstreamFetch('http://query-2:8000/api/v1/chat', { method: 'GET' })
    const refused = await upstreamFetch('http://169.254.169.254/latest/meta-data/', { method: 'GET' }).then(
      () => 'sent',
      (error: unknown) => error,
    )

    expect(fetchStub.getCalls().map((c) => c.args[0])).to.deep.equal([
      'http://query:8000/api/v1/chat',
      'http://query-2:8000/api/v1/chat',
    ])
    expect(refused).to.be.instanceOf(InternalServerError)
    expect(upstreamTarget('http://query:8000/api/v1/chat').upstream).to.equal('query')
  })
})
