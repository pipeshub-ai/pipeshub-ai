import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { z } from 'zod'
import {
  CODE_GRAPH_INSTRUCTIONS,
  CODE_GRAPH_TOOL_NAMES,
  callerHasCodeRepos,
  registerCodeGraphToolDefinitions,
  ToolRegistrar,
} from '../../../src/modules/mcp/code-graph.tools'

type Registered = { config: any; handler: (args: any) => Promise<any> }

function register(): Map<string, Registered> {
  const tools = new Map<string, Registered>()
  const server: ToolRegistrar = {
    registerTool: (name, config, handler) => { tools.set(name, { config, handler }) },
  }
  registerCodeGraphToolDefinitions(server, {
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

  it('says when to use each tool, what it returns and what comes next, in a short description', () => {
    for (const [name, { config }] of register()) {
      const d: string = config.description
      expect(d.length, name).to.be.below(1100)
      expect(d.startsWith('Use '), name).to.equal(true)
      expect(d.indexOf('Next:'), name).to.be.greaterThan(0)
    }
    expect(register().get('pipeshub_code_impact')!.config.description.startsWith('Use first')).to.equal(true)
  })

  it('tells the agent to run impact before grep, in a short paragraph', () => {
    const skill = CODE_GRAPH_INSTRUCTIONS.split('\n\n').find((p) => p.includes('before grep'))!
    expect(skill.length).to.be.below(600)
    expect(skill).to.include('call pipeshub_code_impact')
    expect(skill).to.include('read only the files and')
  })

  it('takes edge_types for impact, refuses an unknown one, and forwards it', async () => {
    const tool = register().get('pipeshub_code_impact')!
    const schema = z.object(tool.config.inputSchema)
    const target = { repo: 'x/y', file_path: 'a.py', qualified_name: 'function:a' }
    expect(schema.safeParse({ ...target, edge_types: ['CALLS', 'OVERRIDES'] }).success).to.equal(true)
    expect(schema.safeParse({ ...target, edge_types: ['CALLZ'] }).success).to.equal(false)
    expect(schema.safeParse({ ...target, edge_types: 'CALLS' }).success).to.equal(false)
    expect(schema.safeParse(target).success).to.equal(true)
    fetchStub.resolves(jsonResponse(200, {}))
    await tool.handler({ ...target, edge_types: ['CALLS'] })
    expect(JSON.parse(fetchStub.firstCall.args[1].body).edge_types).to.deep.equal(['CALLS'])
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
    for (const name of [
      'pipeshub_code_symbols',
      'pipeshub_code_path',
      'pipeshub_code_search',
      'pipeshub_code_semantic_search',
      'pipeshub_code_entry_points',
      'pipeshub_code_impact_of_diff',
    ]) {
      const schema = z.object(tools.get(name)!.config.inputSchema)
      expect(schema.safeParse({}).success, name).to.equal(false)
    }
    const read = z.object(tools.get('pipeshub_code_read')!.config.inputSchema)
    expect(read.safeParse({ record_id: 'r1' }).success).to.equal(true)
    expect(read.safeParse({ record_id: 'r1', lines: 'abc' }).success).to.equal(false)
  })

  it('takes a line range written as a list and sends it as "a-b"', async () => {
    const read = register().get('pipeshub_code_read')!
    const schema = z.object(read.config.inputSchema)
    for (const lines of ['40-80', '[40, 80]', '40,80']) {
      expect(schema.safeParse({ record_id: 'r1', lines }).success, lines).to.equal(true)
    }
    fetchStub.resolves(jsonResponse(200, {}))
    await read.handler({ repo: 'x/y', file_path: 'a.py', lines: '[40, 80]' })
    expect(JSON.parse(fetchStub.firstCall.args[1].body).lines).to.equal('40-80')
  })

  it('names the MCP tools in guidance, never in returned code', async () => {
    fetchStub.resolves(jsonResponse(200, {
      note: 'Call get_neighbour on each `chain` entry next before read_code.',
      code: 'def read_code(): pass',
    }))
    const result = await register().get('pipeshub_code_neighbours')!.handler({ repo: 'x/y', file_path: 'a.py' })
    const out = JSON.parse(result.content[0].text)
    expect(out.note).to.equal('Call pipeshub_code_neighbours on each `chain` entry next before pipeshub_code_read.')
    expect(out.code).to.equal('def read_code(): pass')
  })

  it("names the MCP tools in a coverage gap's note", async () => {
    fetchStub.resolves(jsonResponse(200, {
      neighbors: [],
      coverage: { languages: ['ruby'], note: 'Confirm with search and read_code.' },
    }))
    const result = await register().get('pipeshub_code_neighbours')!.handler({ repo: 'x/y', file_path: 'a.rb' })
    const out = JSON.parse(result.content[0].text)
    expect(out.coverage).to.deep.equal({
      languages: ['ruby'], note: 'Confirm with search and pipeshub_code_read.',
    })
  })

  it('renames the outline, impact and entry_points tools in guidance too', async () => {
    fetchStub.resolves(jsonResponse(200, {
      next: 'Run impact on that symbol alone, then outline or entry_points.',
    }))
    const result = await register().get('pipeshub_code_impact')!.handler({ repo: 'x/y', file_path: 'a.py' })
    expect(JSON.parse(result.content[0].text).next).to.equal(
      'Run pipeshub_code_impact on that symbol alone, then pipeshub_code_outline or ' +
        'pipeshub_code_entry_points.',
    )
  })

  it('bounds the patch impact_of_diff takes', async () => {
    const tool = register().get('pipeshub_code_impact_of_diff')!;
    const schema = z.object(tool.config.inputSchema);
    expect(
      schema.safeParse({ repo: 'x/y', patch: '--- a/a.py\n+++ b/a.py\n' })
        .success,
    ).to.equal(true);
    expect(schema.safeParse({ repo: 'x/y', patch: '' }).success).to.equal(
      false,
    );
    expect(
      schema.safeParse({ repo: 'x/y', patch: 'x'.repeat(2_000_001) }).success,
    ).to.equal(false);
  });

  it('forwards each new tool to its own route', async () => {
    const tools = register()
    const routes: Record<string, string> = {
      pipeshub_code_search: '/search',
      pipeshub_code_semantic_search: '/semantic-search',
      pipeshub_code_outline: '/outline',
      pipeshub_code_impact: '/impact',
      pipeshub_code_entry_points: '/entry-points',
      pipeshub_code_impact_of_diff: '/impact-of-diff',
      pipeshub_code_history: '/history',
    }
    for (const [name, route] of Object.entries(routes)) {
      fetchStub.resetHistory()
      fetchStub.resolves(jsonResponse(200, {}))
      await tools.get(name)!.handler({ repo: 'acme/shop' })
      expect(fetchStub.firstCall.args[0], name).to.equal(
        `http://query:8000/api/v1/code-graph${route}`,
      )
    }
    const search = z.object(tools.get('pipeshub_code_search')!.config.inputSchema)
    expect(search.safeParse({ repo: 'acme/shop', pattern: 'x', mode: 'grep' }).success)
      .to.equal(false)
  })

  describe('callerHasCodeRepos', () => {
    const deps = { aiBackend: 'http://query:8000', authorization: 'Bearer caller-token' }

    it("asks the repo listing with the caller's token and caches the answer", async () => {
      fetchStub.resolves(jsonResponse(200, { repos: [{ repo: 'acme/shop' }] }))
      expect(await callerHasCodeRepos(deps, 'org:has-repos')).to.equal(true)
      expect(await callerHasCodeRepos(deps, 'org:has-repos')).to.equal(true)
      expect(fetchStub.callCount).to.equal(1)
      const [url, init] = fetchStub.firstCall.args
      expect(url).to.equal('http://query:8000/api/v1/code-graph/repos')
      expect(init.headers.authorization).to.equal('Bearer caller-token')
    })

    it('is false when the caller can read no repo', async () => {
      fetchStub.resolves(jsonResponse(200, { repos: [] }))
      expect(await callerHasCodeRepos(deps, 'org:no-repos')).to.equal(false)
    })

    it('is false when the token is refused', async () => {
      fetchStub.resolves(jsonResponse(403, { detail: 'Insufficient scope' }))
      expect(await callerHasCodeRepos(deps, 'org:refused')).to.equal(false)
    })

    it('is true, and not cached, when the query service cannot answer', async () => {
      fetchStub.onFirstCall().resolves(jsonResponse(503, { error: 'graph unavailable' }))
      fetchStub.onSecondCall().rejects(new Error('ECONNREFUSED'))
      fetchStub.onThirdCall().resolves(jsonResponse(200, { repos: [] }))
      expect(await callerHasCodeRepos(deps, 'org:down')).to.equal(true)
      expect(await callerHasCodeRepos(deps, 'org:down')).to.equal(true)
      expect(await callerHasCodeRepos(deps, 'org:down')).to.equal(false)
    })
  })
})
