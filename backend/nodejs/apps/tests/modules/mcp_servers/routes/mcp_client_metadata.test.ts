import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import express from 'express'
import http from 'http'
import { AddressInfo } from 'net'
import {
  createMcpClientMetadataHandler,
  MCP_CLIENT_METADATA_PATH,
  mcpClientMetadataDocument,
} from '../../../../src/modules/mcp_servers/routes/mcp_client_metadata'

// The same table is in the Python test of the client id (`tests/unit/agents/mcp/test_cimd.py`):
// both must build one URL, or the authorization server refuses the client.
const SAME_URL_AS_PYTHON: Array<[string, string]> = [
  ['https://pipeshub.example.com', 'https://pipeshub.example.com/mcp-servers/oauth/client-metadata.json'],
  ['https://pipeshub.example.com/', 'https://pipeshub.example.com/mcp-servers/oauth/client-metadata.json'],
  ['https://pipeshub.example.com//', 'https://pipeshub.example.com/mcp-servers/oauth/client-metadata.json'],
  [' https://example.com/pipeshub/ ', 'https://example.com/pipeshub/mcp-servers/oauth/client-metadata.json'],
]

function keyValueStore(stored: unknown) {
  return {
    get: sinon.stub().callsFake(async () => {
      if (stored instanceof Error) throw stored
      return stored
    }),
  } as any
}

function response() {
  const res: any = { headers: {} as Record<string, string> }
  res.status = sinon.stub().returns(res)
  res.type = sinon.stub().returns(res)
  res.send = sinon.stub().returns(res)
  res.json = sinon.stub().returns(res)
  res.set = sinon.stub().callsFake((name: string, value: string) => {
    res.headers[name] = value
    return res
  })
  return res
}

describe('mcp_servers/routes/mcp_client_metadata', () => {
  afterEach(() => sinon.restore())

  describe('the document', () => {
    SAME_URL_AS_PYTHON.forEach(([configured, clientId]) => {
      it(`has the client id Python builds for ${JSON.stringify(configured)}`, () => {
        const doc = mcpClientMetadataDocument(configured)
        expect(doc.client_id).to.equal(clientId)
        expect(doc.redirect_uris).to.deep.equal([clientId.replace('client-metadata.json', 'callback/')])
      })
    })

    it('is a public client signing in with the authorization code flow', () => {
      expect(mcpClientMetadataDocument('https://pipeshub.example.com')).to.deep.equal({
        client_id: 'https://pipeshub.example.com/mcp-servers/oauth/client-metadata.json',
        client_name: 'PipesHub',
        client_uri: 'https://pipeshub.example.com',
        redirect_uris: ['https://pipeshub.example.com/mcp-servers/oauth/callback/'],
        grant_types: ['authorization_code', 'refresh_token'],
        response_types: ['code'],
        token_endpoint_auth_method: 'none',
      })
    })
  })

  describe('the handler', () => {
    it('serves it for the configured public address, cacheable', async () => {
      const store = keyValueStore(JSON.stringify({ frontend: { publicEndpoint: 'https://pipeshub.example.com/' } }))
      const res = response()

      await createMcpClientMetadataHandler(store)({} as any, res, sinon.stub())

      expect(store.get.calledWith('/services/endpoints')).to.be.true
      expect(res.json.firstCall.args[0].client_id).to.equal(
        'https://pipeshub.example.com/mcp-servers/oauth/client-metadata.json',
      )
      expect(res.headers['Cache-Control']).to.equal('public, max-age=300')
    })

    it('reads the address on each request, so a change applies at once', async () => {
      const store = keyValueStore(JSON.stringify({ frontend: { publicEndpoint: 'https://old.example.com' } }))
      const handler = createMcpClientMetadataHandler(store)
      await handler({} as any, response(), sinon.stub())

      store.get.callsFake(async () => JSON.stringify({ frontend: { publicEndpoint: 'https://new.example.com' } }))
      const res = response()
      await handler({} as any, res, sinon.stub())

      expect(res.json.firstCall.args[0].client_uri).to.equal('https://new.example.com')
    })

    it('is not found without a configured address', async () => {
      for (const stored of [null, '{}', JSON.stringify({ frontend: { publicEndpoint: '  ' } })]) {
        const res = response()
        await createMcpClientMetadataHandler(keyValueStore(stored))({} as any, res, sinon.stub())
        expect(res.status.calledWith(404)).to.be.true
        expect(res.json.called).to.be.false
      }
    })

    it('passes a store failure on', async () => {
      const next = sinon.stub()
      const failure = new Error('etcd is down')
      await createMcpClientMetadataHandler(keyValueStore(failure))({} as any, response(), next)
      expect(next.calledWith(failure)).to.be.true
    })

    it('answers ahead of the SPA fallback, as JSON', async () => {
      const app = express()
      const store = keyValueStore(JSON.stringify({ frontend: { publicEndpoint: 'https://pipeshub.example.com' } }))
      app.get(MCP_CLIENT_METADATA_PATH, createMcpClientMetadataHandler(store))
      app.get('*', (_req, res) => {
        res.status(404).type('text/plain').send('Not Found')
      })
      const server = http.createServer(app).listen(0)
      try {
        const { port } = server.address() as AddressInfo
        const answer = await new Promise<{ status?: number; type?: string; body: string }>((resolve, reject) => {
          http.get(`http://127.0.0.1:${port}${MCP_CLIENT_METADATA_PATH}`, (res) => {
            let body = ''
            res.on('data', (chunk) => (body += chunk))
            res.on('end', () => resolve({ status: res.statusCode, type: res.headers['content-type'], body }))
          }).on('error', reject)
        })
        expect(answer.status).to.equal(200)
        expect(answer.type).to.match(/^application\/json/)
        expect(JSON.parse(answer.body).client_id).to.equal(
          'https://pipeshub.example.com/mcp-servers/oauth/client-metadata.json',
        )
      } finally {
        server.close()
      }
    })
  })
})
