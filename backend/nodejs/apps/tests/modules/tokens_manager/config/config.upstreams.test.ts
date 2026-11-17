import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { loadAppConfig } from '../../../../src/modules/tokens_manager/config/config'
import { ConfigService } from '../../../../src/modules/tokens_manager/services/cm.service'
import { registerUpstreams, upstreamTarget } from '../../../../src/libs/http/upstream-call'

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
})
