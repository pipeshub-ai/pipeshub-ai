import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { z } from 'zod'
import {
  CODE_GRAPH_TOOL_NAMES,
  registerCodeGraphTools,
  ToolRegistrar,
} from '../../../src/modules/mcp/code-graph.tools'

type Registered = { config: any; handler: (args: any) => Promise<any> }

function register(): Map<string, Registered> {
  const tools = new Map<string, Registered>()
  const server: ToolRegistrar = {
    registerTool: (name, config, handler) => { tools.set(name, { config, handler }) },
  }
  registerCodeGraphTools(server, {
    aiBackend: 'http://query:8000',
    authorization: 'Bearer caller-token',
  })
  return tools
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('MCP code-graph tools', () => {
  let fetchStub: sinon.SinonStub

  beforeEach(() => {
    fetchStub = sinon.stub(globalThis, 'fetch')
  })

  afterEach(() => sinon.restore())

  it('registers every tool read-only', () => {
    const tools = register()
    expect([...tools.keys()]).to.deep.equal([...CODE_GRAPH_TOOL_NAMES])
    for (const { config } of tools.values()) {
      expect(config.annotations.readOnlyHint).to.equal(true)
      expect(config.annotations.destructiveHint).to.equal(false)
    }
  })

  it("forwards the caller's own token to the query service", async () => {
    fetchStub.resolves(jsonResponse(200, { neighbors: [], repo: 'acme/shop' }))
    const result = await register().get('pipeshub_code_neighbours')!.handler({
      repo: 'acme/shop', file_path: 'app/main.py', direction: 'inbound',
    })

    const [url, init] = fetchStub.firstCall.args
    expect(url).to.equal('http://query:8000/api/v1/code-graph/neighbours')
    expect(init.method).to.equal('POST')
    expect(init.headers.authorization).to.equal('Bearer caller-token')
    expect(JSON.parse(init.body)).to.deep.equal({
      repo: 'acme/shop', file_path: 'app/main.py', direction: 'inbound',
    })
    expect(result.isError).to.equal(undefined)
    expect(JSON.parse(result.content[0].text).repo).to.equal('acme/shop')
  })

  it('lists repos with a GET and no body', async () => {
    fetchStub.resolves(jsonResponse(200, { repos: [] }))
    await register().get('pipeshub_code_repos')!.handler({})
    const [url, init] = fetchStub.firstCall.args
    expect(url).to.equal('http://query:8000/api/v1/code-graph/repos')
    expect(init.method).to.equal('GET')
    expect(init.body).to.equal(undefined)
  })

  it('marks a refused or failed call as an error and keeps its message', async () => {
    fetchStub.resolves(jsonResponse(404, { error: 'No indexed repository you can read matches' }))
    const result = await register().get('pipeshub_code_read')!.handler({ repo: 'x/y', file_path: 'a.py' })
    expect(result.isError).to.equal(true)
    expect(result.content[0].text).to.include('No indexed repository')
  })

  it('reports an error payload in a 200 as an error', async () => {
    fetchStub.resolves(jsonResponse(200, { error: 'a.py is a test file' }))
    const result = await register().get('pipeshub_code_read')!.handler({ repo: 'x/y', file_path: 'a.py' })
    expect(result.isError).to.equal(true)
  })

  it('turns a transport failure into an error result instead of throwing', async () => {
    fetchStub.rejects(new Error('ECONNREFUSED'))
    const result = await register().get('pipeshub_code_path')!.handler({
      repo: 'x/y', file_path_a: 'a.py', qualified_name_a: 'function:a',
      file_path_b: 'b.py', qualified_name_b: 'function:b',
    })
    expect(result.isError).to.equal(true)
    expect(result.content[0].text).to.include('code graph unavailable')
  })

  it('requires repo where no record id can stand in for it', () => {
    const tools = register()
    for (const name of ['pipeshub_code_symbols', 'pipeshub_code_path']) {
      const schema = z.object(tools.get(name)!.config.inputSchema)
      expect(schema.safeParse({}).success, name).to.equal(false)
    }
    const read = z.object(tools.get('pipeshub_code_read')!.config.inputSchema)
    expect(read.safeParse({ record_id: 'r1' }).success).to.equal(true)
    expect(read.safeParse({ record_id: 'r1', lines: 'abc' }).success).to.equal(false)
  })
})
