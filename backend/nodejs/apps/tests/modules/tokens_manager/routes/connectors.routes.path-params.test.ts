import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  MEMBER,
  ORG_A,
  Harness,
  call,
  errorMessage,
  rawCall,
  sessionToken,
  startHarness,
} from './connectors-http-harness'
import { createOAuthRouter } from '../../../../src/modules/tokens_manager/routes/oauth.routes'
import { createToolsetsRouter } from '../../../../src/modules/toolsets/routes/toolsets_routes'
import { createAgentConversationalRouter } from '../../../../src/modules/enterprise_search/routes/es.routes'
import { INVALID_PATH_SEGMENT_MESSAGE } from '../../../../src/libs/middlewares/safe-path-params.middleware'

const member = MEMBER

// Express decodes %2F and %3F inside a path parameter, and the controllers
// interpolate the parameter into the connector-service URL, where `..` and `?`
// are structural again.
const HOSTILE_IDS = ['..%2F..%2Fadmin', 'vector-store%2Fcleanup%3F', 'abc%3Fx%3D1', 'a%23b', 'a%5Cb']

const ROUTES: Array<{ method: string; path: (id: string) => string; body?: unknown }> = [
  { method: 'GET', path: (id) => `/${id}/stats` },
  { method: 'POST', path: (id) => `/${id}/reindex`, body: {} },
  { method: 'POST', path: (id) => `/${id}/resync`, body: { connectorName: 'Google Drive' } },
  { method: 'PUT', path: (id) => `/${id}/config`, body: {} },
  { method: 'PUT', path: (id) => `/${id}/config/auth`, body: { auth: {} } },
  { method: 'PUT', path: (id) => `/${id}/config/filters-sync`, body: {} },
  { method: 'PUT', path: (id) => `/${id}/name`, body: { instanceName: 'Renamed' } },
  { method: 'GET', path: (id) => `/${id}/oauth/authorize` },
  { method: 'POST', path: (id) => `/${id}/filters`, body: { filters: {} } },
  { method: 'GET', path: (id) => `/${id}/filters/folders/options` },
  { method: 'POST', path: (id) => `/${id}/toggle`, body: { type: 'sync' } },
]

describe('Connector routes: path parameters stay inside their URL segment', () => {
  let h: Harness

  beforeEach(async () => {
    h = await startHarness()
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  it("does not let a member reach the admin-only vector store cleanup through reindex's connector id", async () => {
    h.backend.on('POST', '/api/v1/connectors/vector-store/cleanup', { status: 202, body: { success: true } })

    const r = await call(h, 'POST', '/vector-store%2Fcleanup%3F/reindex', sessionToken(h, member), {})

    expect(h.backend.callsTo('POST', '/api/v1/connectors/vector-store/cleanup')).to.have.length(0)
    expect(r.status).to.equal(400)
  })

  for (const route of ROUTES) {
    it(`${route.method} ${route.path(':connectorId')} rejects connector ids that are not a plain key`, async () => {
      const token = sessionToken(h, member)
      for (const id of HOSTILE_IDS) {
        const r = await call(h, route.method, route.path(id), token, route.body)
        expect(r.status, `status for ${id}`).to.equal(400)
      }
      expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([])
    })
  }

  it('rejects a filter key that would climb out of the options path', async () => {
    const r = await call(
      h,
      'GET',
      '/3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90/filters/..%2F..%2F..%2Fstats%3F/options',
      sessionToken(h, member),
    )

    expect(r.status).to.equal(400)
    expect(h.backend.calls).to.have.length(0)
  })

  // Only these four routes checked the connector-id pattern before this change;
  // the others keep accepting any path-safe id so a stored key of another
  // shape is never locked out.
  it('keeps the connector-id pattern on the routes that already had it', async () => {
    const token = sessionToken(h, member)
    for (const [method, path] of [
      ['GET', '/legacy.key'],
      ['DELETE', '/legacy.key'],
      ['GET', '/legacy.key/config'],
      ['GET', '/legacy.key/filters'],
    ] as const) {
      const r = await call(h, method, path, token)
      expect(r.status, `${method} ${path}`).to.equal(400)
    }
    expect(h.backend.calls).to.have.length(0)
  })

  for (const id of ['legacy.key:01', 'x'.repeat(80)]) {
    it(`still forwards a path-safe stored id of another shape (${id.slice(0, 16)}) on the other routes`, async () => {
      h.backend.on('POST', `/api/v1/connectors/${id}/reindex`, { status: 200, body: { success: true } })
      h.backend.on('PUT', `/api/v1/connectors/${id}/name`, { status: 200, body: { success: true } })
      const token = sessionToken(h, member)

      const reindex = await call(h, 'POST', `/${id}/reindex`, token, {})
      const rename = await call(h, 'PUT', `/${id}/name`, token, { instanceName: 'Renamed' })

      expect(reindex.status).to.equal(200)
      expect(rename.status).to.equal(200)
      expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([
        `POST /api/v1/connectors/${id}/reindex`,
        `PUT /api/v1/connectors/${id}/name`,
      ])
    })
  }

  for (const id of ['3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90', `knowledgeBase_${ORG_A}`]) {
    it(`still forwards a real connector id (${id}) to its own path`, async () => {
      h.backend.on('POST', `/api/v1/connectors/${id}/reindex`, { status: 200, body: { success: true } })
      h.backend.on('GET', `/api/v1/connectors/${id}/filters/space_keys/options`, { status: 200, body: { options: [] } })
      const token = sessionToken(h, member)

      const reindex = await call(h, 'POST', `/${id}/reindex`, token, {})
      const options = await call(h, 'GET', `/${id}/filters/space_keys/options`, token)

      expect(reindex.status).to.equal(200)
      expect(options.status).to.equal(200)
      expect(h.backend.calls.map((c) => c.path)).to.deep.equal([
        `/api/v1/connectors/${id}/reindex`,
        `/api/v1/connectors/${id}/filters/space_keys/options`,
      ])
    })
  }
})

describe('Routers that proxy to the connector service: crafted ids cannot reach another endpoint', () => {
  let h: Harness
  const CLEANUP = '/api/v1/connectors/vector-store/cleanup'

  beforeEach(async () => {
    h = await startHarness({
      extraRouters: [
        { mountPath: '/api/v1/oauth', create: createOAuthRouter },
        { mountPath: '/api/v1/toolsets', create: createToolsetsRouter },
      ],
    })
    h.backend.on('POST', CLEANUP, { status: 202, body: { success: true } })
    h.backend.on('GET', '/api/v1/connectors/active', { status: 200, body: { connectors: [] } })
    h.backend.on('DELETE', '/api/v1/connectors/3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90', { status: 200, body: { success: true } })
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  const CRAFTED: Array<{ label: string; method: string; path: string; body?: unknown }> = [
    {
      label: 'OAuth config create, connector type climbing to cleanup',
      method: 'POST',
      path: '/api/v1/oauth/..%2Fconnectors%2Fvector-store%2Fcleanup%3F',
      body: { oauthInstanceName: 'x', config: {}, baseUrl: 'https://app.acme.test' },
    },
    {
      label: 'OAuth config delete, connector type climbing to connector instance delete',
      method: 'DELETE',
      path: '/api/v1/oauth/..%2Fconnectors/3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90',
    },
    {
      label: 'toolset config save, toolset id climbing to cleanup',
      method: 'POST',
      path: '/api/v1/toolsets/..%2Fconnectors%2Fvector-store%2Fcleanup%3F/config',
      body: { auth: { type: 'oauth' } },
    },
    {
      label: 'connector schema, connector type climbing to the active list',
      method: 'GET',
      path: '/api/v1/connectors/registry/..%2F..%2Factive%3Fx/schema',
    },
    { label: 'record content, record id of ..', method: 'GET', path: '/api/v1/connectors/record/%2E%2E/content' },
    { label: 'record content, record id of .', method: 'GET', path: '/api/v1/connectors/record/%2E/content' },
    {
      label: 'OAuth config read, double-encoded slash',
      method: 'GET',
      path: '/api/v1/oauth/GOOGLE%252F..%252Fx/cfg-1',
    },
    { label: 'toolset config read, backslash', method: 'GET', path: '/api/v1/toolsets/a%5C..%5Cb/config' },
  ]

  for (const shape of CRAFTED) {
    it(`refuses ${shape.label} with a 400 and calls nothing`, async () => {
      const r = await rawCall(h, shape.method, shape.path, sessionToken(h, member), shape.body)

      expect(r.status).to.equal(400)
      expect(errorMessage(r)).to.equal(INVALID_PATH_SEGMENT_MESSAGE)
      expect(h.backend.callsTo('POST', CLEANUP)).to.have.length(0)
      expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([])
    })
  }

  it('still lists OAuth configs for a connector type with spaces', async () => {
    h.backend.on('GET', '/api/v1/oauth/SHAREPOINT%20ONLINE', { status: 200, body: { oauthConfigs: [] } })

    const r = await rawCall(h, 'GET', '/api/v1/oauth/SHAREPOINT%20ONLINE', sessionToken(h, member))

    expect(r.status).to.equal(200)
    expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal(['GET /api/v1/oauth/SHAREPOINT%20ONLINE'])
  })

  it('still reads the schema for a connector type with spaces', async () => {
    const path = '/api/v1/connectors/registry/CONFLUENCE%20DATA%20CENTER%20PERSONAL/schema'
    h.backend.on('GET', path, { status: 200, body: { schema: {} } })

    const r = await rawCall(h, 'GET', path, sessionToken(h, member))

    expect(r.status).to.equal(200)
    expect(h.backend.calls.map((c) => c.path)).to.deep.equal([path])
  })

  it('still saves a toolset config and reads record content for real ids', async () => {
    const toolsetId = '9b1d5c2e-7f3a-4e8b-a6c1-0d2e4f6a8b1c'
    const recordId = '5f0c7e2a-3b9d-4c1e-8a7f-6d2b0e9c4a13'
    h.backend.on('POST', `/api/v1/toolsets/${toolsetId}/config`, { status: 200, body: { success: true } })
    h.backend.on('GET', `/api/v1/records/${recordId}/content`, { status: 200, body: { content: 'x' } })
    const token = sessionToken(h, member)

    const saved = await rawCall(h, 'POST', `/api/v1/toolsets/${toolsetId}/config`, token, { auth: { type: 'oauth' } })
    const content = await rawCall(h, 'GET', `/api/v1/connectors/record/${recordId}/content`, token)

    expect(saved.status).to.equal(200)
    expect(content.status).to.equal(200)
    expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([
      `POST /api/v1/toolsets/${toolsetId}/config`,
      `GET /api/v1/records/${recordId}/content`,
    ])
  })
})

describe('Agent routes that proxy to the AI service: dot segments, padded or not, are refused', () => {
  let h: Harness
  const AGENT_KEY = '0c7f4d2a-9e1b-4a6c-8d3f-5b2e7a9c1d40'

  beforeEach(async () => {
    h = await startHarness({
      extraRouters: [{ mountPath: '/api/v1/agents', create: createAgentConversationalRouter }],
    })
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  const DOT_SHAPES: Array<{ label: string; method: string; path: string }> = [
    { label: 'attachment delete, record id of ..', method: 'DELETE', path: `/api/v1/agents/${AGENT_KEY}/conversations/attachments/%2E%2E` },
    { label: 'attachment delete, record id of padded ..', method: 'DELETE', path: `/api/v1/agents/${AGENT_KEY}/conversations/attachments/%20%2E%2E%20` },
    { label: 'web search usage, provider of padded ..', method: 'GET', path: '/api/v1/agents/web-search-usage/%20%2E%2E' },
    { label: 'model usage, model key of padded .', method: 'GET', path: '/api/v1/agents/model-usage/%2E%20' },
    { label: 'web search usage, provider of only spaces', method: 'GET', path: '/api/v1/agents/web-search-usage/%20%20' },
  ]

  for (const shape of DOT_SHAPES) {
    it(`refuses ${shape.label} with a 400 and makes no upstream call`, async () => {
      const r = await rawCall(h, shape.method, shape.path, sessionToken(h, member))

      expect(r.status).to.equal(400)
      expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([])
    })
  }

  it('still deletes a real attachment and reads usage for a real provider', async () => {
    const recordId = '5f0c7e2a-3b9d-4c1e-8a7f-6d2b0e9c4a13'
    h.backend.on('DELETE', `/api/v1/chat/attachments/${recordId}`, { status: 204 })
    h.backend.on('GET', '/api/v1/agent/web-search-usage/tavily', { status: 200, body: { success: true, agents: [] } })
    const token = sessionToken(h, member)

    const removed = await rawCall(h, 'DELETE', `/api/v1/agents/${AGENT_KEY}/conversations/attachments/${recordId}`, token)
    const usage = await rawCall(h, 'GET', '/api/v1/agents/web-search-usage/Tavily', token)

    expect(removed.status).to.equal(204)
    expect(usage.status).to.equal(200)
    expect(h.backend.calls.map((c) => `${c.method} ${c.path}`)).to.deep.equal([
      `DELETE /api/v1/chat/attachments/${recordId}`,
      'GET /api/v1/agent/web-search-usage/tavily',
    ])
  })
})
