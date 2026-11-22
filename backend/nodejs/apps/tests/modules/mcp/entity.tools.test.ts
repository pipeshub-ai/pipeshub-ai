import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { z } from 'zod'
import {
  ALL_ENTITY_CAPABILITIES,
  ENTITIES_TOOL,
  FIND_RECORDS_TOOL,
  EntityCapabilities,
  NO_ENTITY_CAPABILITIES,
  callerEntityCapabilities,
  entityActions,
  entityInstructions,
  entityToolNames,
  registerEntityToolDefinitions,
  registerEntityTools,
  resetEntityCapabilitiesCache,
  rewriteGuidance,
  withoutDisabledEntityTools,
} from '../../../src/modules/mcp/entity.tools'
import { ToolRegistrar } from '../../../src/modules/mcp/code-graph.tools'
import { registerUpstreams } from '../../../src/libs/http/upstream-call'
import { registerAppUpstreams } from '../../../src/modules/tokens_manager/config/config'

type Registered = { config: any; handler: (args: any) => Promise<any> }

const DEPS = { aiBackend: 'http://query:8000', authorization: 'Bearer caller-token' }

function register(caps: EntityCapabilities = ALL_ENTITY_CAPABILITIES): Map<string, Registered> {
  const tools = new Map<string, Registered>()
  const server: ToolRegistrar = {
    registerTool: (name, config, handler) => { tools.set(name, { config, handler }) },
  }
  registerEntityToolDefinitions(server, DEPS, caps)
  return tools
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

const caps = (on: Partial<EntityCapabilities>): EntityCapabilities => ({ ...NO_ENTITY_CAPABILITIES, ...on })

// What the query service reports for each way a deployment can be set up
// (entity_tools.capabilities_of): the store, extraction, the ENTITY_SEARCH_TOOL switch.
const DEPLOYMENTS: Array<{ name: string; caps: EntityCapabilities; entities: string[]; findRecords: string[] }> = [
  {
    name: 'nothing indexed into the entity store, extraction off',
    caps: caps({ recordsByPerson: true }),
    entities: [],
    findRecords: ['by_person'],
  },
  {
    name: 'the entity store, extraction off',
    caps: caps({ entitySearch: true, entityRecords: true, recordsByPerson: true }),
    entities: ['search', 'records'],
    findRecords: ['by_person'],
  },
  {
    name: 'extraction on, the switch off',
    caps: caps({ entitySearch: true, entityRecords: true, namedEntities: true, coMentions: true, recordsByValue: true, recordsByPerson: true }),
    entities: ['search', 'records', 'co_mentioned'],
    findRecords: ['by_person', 'by_value'],
  },
  {
    name: 'everything on',
    caps: ALL_ENTITY_CAPABILITIES,
    entities: ['search', 'records', 'co_mentioned'],
    findRecords: ['by_person', 'by_value', 'by_entities'],
  },
  {
    name: 'extraction without the store',
    caps: caps({ namedEntities: true, coMentions: true, recordsByValue: true, recordsByPerson: true }),
    entities: ['co_mentioned'],
    findRecords: ['by_person', 'by_value'],
  },
]

describe('MCP entity tools', () => {
  beforeEach(() => registerAppUpstreams({ aiBackend: 'http://query:8000' }))
  afterEach(() => registerUpstreams({}))

  let fetchStub: sinon.SinonStub

  beforeEach(() => {
    fetchStub = sinon.stub(globalThis, 'fetch')
    resetEntityCapabilitiesCache()
  })

  afterEach(() => sinon.restore())

  describe('what each deployment serves', () => {
    for (const d of DEPLOYMENTS) {
      it(`${d.name}: only the actions served are mounted`, () => {
        const tools = register(d.caps)
        expect(entityActions(d.caps)).to.deep.equal({ entities: d.entities, findRecords: d.findRecords })
        const expected = [...(d.entities.length ? [ENTITIES_TOOL] : []), ...(d.findRecords.length ? [FIND_RECORDS_TOOL] : [])]
        expect([...tools.keys()]).to.deep.equal(expected)
        expect(entityToolNames(d.caps)).to.deep.equal(expected)
        if (d.entities.length) {
          const action = tools.get(ENTITIES_TOOL)!.config.inputSchema.action
          expect(action.options).to.deep.equal(d.entities)
        }
        if (d.findRecords.length) {
          const action = tools.get(FIND_RECORDS_TOOL)!.config.inputSchema.action
          expect(action.options).to.deep.equal(d.findRecords)
        }
      })
    }

    it('mounts nothing when nothing is served', () => {
      expect([...register(NO_ENTITY_CAPABILITIES).keys()]).to.deep.equal([])
      expect(entityInstructions(NO_ENTITY_CAPABILITIES)).to.equal('')
    })

    it('refuses, before any call, an action this deployment does not serve', async () => {
      const tool = register(DEPLOYMENTS[2].caps).get(FIND_RECORDS_TOOL)!
      const schema = z.object(tool.config.inputSchema)
      expect(schema.safeParse({ action: 'by_entities', mentions: [{ name: 'Acme' }] }).success).to.equal(false)
      // A client calling past the listing gets an error, never a request.
      const result = await tool.handler({ action: 'by_entities', mentions: [{ name: 'Acme' }] })
      expect(result.isError).to.equal(true)
      expect(fetchStub.called).to.equal(false)
    })

    it('leaves out the named-entity type and kinds while extraction is off', () => {
      const off = register(DEPLOYMENTS[1].caps).get(ENTITIES_TOOL)!.config.inputSchema
      expect(off.named_entity_kinds).to.equal(undefined)
      expect(off.entity_types.unwrap().element.options).to.not.include('named_entity')
      expect(off.entity_type.unwrap().options).to.not.include('named_entity')
      expect(off.entity_ids, 'no co_mentioned parameters').to.equal(undefined)
      const on = register(ALL_ENTITY_CAPABILITIES).get(ENTITIES_TOOL)!.config.inputSchema
      expect(on.named_entity_kinds).to.not.equal(undefined)
      expect(on.entity_type.unwrap().options).to.include('named_entity')
      // A search can be narrowed only to the published types; records list people and organizations too.
      expect(on.entity_types.unwrap().element.options).to.not.include('person')
      expect(on.entity_type.unwrap().options).to.include.members(['person', 'organization'])
    })

    it('publishes only the parameters of the actions served', () => {
      const personOnly = register(DEPLOYMENTS[0].caps).get(FIND_RECORDS_TOOL)!.config.inputSchema
      expect(Object.keys(personOnly).sort()).to.deep.equal(['action', 'cursor', 'limit', 'person', 'record_types', 'roles'])
      const all = register().get(FIND_RECORDS_TOOL)!.config.inputSchema
      for (const key of ['amount_min', 'date_from', 'mentions', 'match', 'query']) expect(all[key], key).to.not.equal(undefined)
    })

    it('takes no more names than the query service looks up', () => {
      const schema = z.object(register().get(FIND_RECORDS_TOOL)!.config.inputSchema)
      const names = (n: number) => Array.from({ length: n }, (_, i) => ({ name: `n${i}` }))
      expect(schema.safeParse({ action: 'by_entities', mentions: names(5) }).success).to.equal(true)
      expect(schema.safeParse({ action: 'by_entities', mentions: names(6) }).success).to.equal(false)
    })

    it('describes only the actions served', () => {
      const d = register(DEPLOYMENTS[0].caps).get(FIND_RECORDS_TOOL)!.config.description as string
      expect(d).to.include("a person's tickets")
      expect(d).to.not.include('amounts')
      expect(d).to.not.include('passages')
    })

    it('registers every tool read-only and as a closed object', () => {
      const tools = new Map<string, any>()
      registerEntityTools({ registerTool: (name, config) => { tools.set(name, config) } }, DEPS, ALL_ENTITY_CAPABILITIES)
      for (const [name, config] of tools) {
        expect(config.annotations.readOnlyHint, name).to.equal(true)
        expect(config.annotations.destructiveHint, name).to.equal(false)
        const json = z.toJSONSchema(config.inputSchema) as any
        expect(json.additionalProperties, name).to.equal(false)
        expect(json.required, name).to.deep.equal(['action'])
      }
    })

    it('takes out a tool an admin turned off, with its actions', () => {
      expect(entityToolNames(withoutDisabledEntityTools(ALL_ENTITY_CAPABILITIES, [ENTITIES_TOOL]))).to.deep.equal([FIND_RECORDS_TOOL])
      expect(entityToolNames(withoutDisabledEntityTools(ALL_ENTITY_CAPABILITIES, [FIND_RECORDS_TOOL]))).to.deep.equal([ENTITIES_TOOL])
      expect(entityToolNames(withoutDisabledEntityTools(ALL_ENTITY_CAPABILITIES, [ENTITIES_TOOL, FIND_RECORDS_TOOL]))).to.deep.equal([])
    })

    it('names in the instructions only the actions served', () => {
      const text = entityInstructions(DEPLOYMENTS[1].caps)
      expect(text).to.include('action:"search"').and.include('action:"by_person"')
      expect(text).to.not.include('by_value').and.not.include('by_entities').and.not.include('co_mentioned')
      expect(entityInstructions(ALL_ENTITY_CAPABILITIES)).to.include('by_entities').and.include('co_mentioned')
    })
  })

  describe('calls', () => {
    it("searches entities with the caller's token and returns the service's answer", async () => {
      fetchStub.resolves(jsonResponse(200, { status: 'success', results: [{ entityId: 'e1', entityType: 'topic', name: 'Roadmap' }] }))
      const result = await register().get(ENTITIES_TOOL)!.handler({
        action: 'search', query: 'roadmap', entity_types: ['topic'], named_entity_kinds: ['product'], limit: 40,
      })
      const [url, init] = fetchStub.firstCall.args
      expect(url).to.equal('http://query:8000/api/v1/search/entities')
      expect(init.method).to.equal('POST')
      expect(init.headers.authorization).to.equal('Bearer caller-token')
      // The route takes at most 25 entities.
      expect(JSON.parse(init.body)).to.deep.equal({ query: 'roadmap', entityTypes: ['topic'], namedEntityKinds: ['product'], limit: 25 })
      expect(result.isError).to.equal(undefined)
      expect(JSON.parse(result.content[0].text).results[0].entityId).to.equal('e1')
    })

    it('never sends named-entity kinds while extraction is off, even if a client passes them', async () => {
      fetchStub.resolves(jsonResponse(200, { status: 'success', results: [] }))
      await register(DEPLOYMENTS[1].caps).get(ENTITIES_TOOL)!.handler({ action: 'search', query: 'x', named_entity_kinds: ['product'] })
      expect(JSON.parse(fetchStub.firstCall.args[1].body)).to.deep.equal({ query: 'x' })
    })

    const ROUTES: Array<[string, string, Record<string, unknown>, string, Record<string, unknown>]> = [
      [ENTITIES_TOOL, 'records', { entity_id: 'e1', entity_type: 'topic', record_types: ['FILE'], limit: 5, cursor: 'c' },
        '/search/entities/records', { entityId: 'e1', entityType: 'topic', recordTypes: ['FILE'], limit: 5, cursor: 'c' }],
      [ENTITIES_TOOL, 'co_mentioned', { entity_ids: ['a', 'b'], kinds: ['person'], limit: 3 },
        '/search/entities/co-mentioned', { entityIds: ['a', 'b'], kinds: ['person'], limit: 3 }],
      [FIND_RECORDS_TOOL, 'by_person', { person: 'me', roles: ['assignee'], record_types: ['TICKET'], cursor: 'c' },
        '/search/records/by-person', { person: 'me', roles: ['assignee'], recordTypes: ['TICKET'], cursor: 'c' }],
      [FIND_RECORDS_TOOL, 'by_value', { amount_min: 50000, currency: 'USD', date_from: '2026-03-01', percent_max: 0.2, source_ids: ['kb'], name: 'Acme', entity_ids: ['n1'] },
        '/search/records/by-value', { amountMin: 50000, currency: 'USD', dateFrom: '2026-03-01', percentMax: 0.2, sourceIds: ['kb'], name: 'Acme', entityIds: ['n1'] }],
      [FIND_RECORDS_TOOL, 'by_entities', { mentions: [{ name: 'Acme', kind: 'organization' }, { name: 'Globex' }], match: 'all', query: 'renewal', limit: 5 },
        '/search/records/by-entities', { mentions: [{ name: 'Acme', kind: 'organization' }, { name: 'Globex' }], match: 'all', query: 'renewal', limit: 5 }],
    ]
    for (const [tool, action, args, path, body] of ROUTES) {
      it(`${tool} ${action} posts to ${path} with only the arguments given`, async () => {
        fetchStub.resolves(jsonResponse(200, { text: 'ok' }))
        const schema = z.object(register().get(tool)!.config.inputSchema)
        expect(schema.safeParse({ action, ...args }).success, 'the published schema accepts it').to.equal(true)
        await register().get(tool)!.handler({ action, ...args })
        const [url, init] = fetchStub.firstCall.args
        expect(url).to.equal(`http://query:8000/api/v1${path}`)
        expect(JSON.parse(init.body)).to.deep.equal(body)
      })
    }

    it('returns a listing as the text it is, not a JSON string', async () => {
      fetchStub.resolves(jsonResponse(200, { text: 'Records linked to Ada, newest first:\n- r1' }))
      const result = await register().get(FIND_RECORDS_TOOL)!.handler({ action: 'by_person', person: 'Ada' })
      expect(result.content[0].text).to.equal('Records linked to Ada, newest first:\n- r1')
    })

    const MISSING: Array<[string, Record<string, unknown>, string]> = [
      [ENTITIES_TOOL, { action: 'search' }, 'query'],
      [ENTITIES_TOOL, { action: 'search', query: '  ' }, 'query'],
      [ENTITIES_TOOL, { action: 'records', entity_id: 'e1' }, 'entity_type'],
      [ENTITIES_TOOL, { action: 'co_mentioned', entity_ids: [] }, 'entity_ids'],
      [FIND_RECORDS_TOOL, { action: 'by_person' }, 'person'],
      [FIND_RECORDS_TOOL, { action: 'by_entities' }, 'mentions'],
    ]
    for (const [tool, args, needed] of MISSING) {
      it(`${tool} ${String(args.action)} without ${needed} is an error and calls nothing`, async () => {
        const result = await register().get(tool)!.handler(args)
        expect(result.isError).to.equal(true)
        expect(result.content[0].text).to.include(needed)
        expect(fetchStub.called).to.equal(false)
      })
    }

    it("keeps the service's reason when it refuses or cannot answer", async () => {
      fetchStub.resolves(jsonResponse(404, { detail: 'Named-entity lookup is not enabled.' }))
      let result = await register().get(ENTITIES_TOOL)!.handler({ action: 'co_mentioned', entity_ids: ['e1'] })
      expect(result.isError).to.equal(true)
      expect(result.content[0].text).to.include('Named-entity lookup is not enabled.')
      fetchStub.resolves(jsonResponse(503, { detail: 'Lookup failed — try again.' }))
      result = await register().get(ENTITIES_TOOL)!.handler({ action: 'records', entity_id: 'e1', entity_type: 'topic' })
      expect(result.isError).to.equal(true)
      expect(result.content[0].text).to.include('Lookup failed')
    })

    it('turns a transport failure into an error result instead of throwing', async () => {
      fetchStub.rejects(new Error('connect ECONNREFUSED'))
      const result = await register().get(FIND_RECORDS_TOOL)!.handler({ action: 'by_person', person: 'me' })
      expect(result.isError).to.equal(true)
      expect(result.content[0].text).to.include('unavailable')
    })

    it('names the MCP tools in the guidance of a listing it returns', async () => {
      fetchStub.resolves(jsonResponse(200, {
        text: 'More records may exist: knowledgegraph__find_records_by_person(person="ada@x.test", cursor="40")',
      }))
      const result = await register().get(FIND_RECORDS_TOOL)!.handler({ action: 'by_person', person: 'Ada' })
      expect(result.content[0].text).to.equal(
        'More records may exist: pipeshub_find_records(action="by_person", person="ada@x.test", cursor="40")',
      )
    })

    it('rewrites the resolution and message of a passage search, not the passages', async () => {
      fetchStub.resolves(jsonResponse(200, {
        entities: 'Entities:\n- "Acme" — also see knowledgegraph__find_records_by_entity(entity_id="n1", entity_type="named_entity")',
        chunks: [{ text: 'call knowledgegraph__search in code', record: { recordId: 'r1' } }],
      }))
      const result = await register().get(FIND_RECORDS_TOOL)!.handler({ action: 'by_entities', mentions: [{ name: 'Acme' }] })
      const data = JSON.parse(result.content[0].text)
      expect(data.entities).to.include('pipeshub_entities(action="records", entity_id="n1", entity_type="named_entity")')
      expect(data.chunks[0].text).to.equal('call knowledgegraph__search in code')
    })
  })

  describe('rewriteGuidance', () => {
    const CASES: Array<[string, string]> = [
      ['knowledgegraph__fetch_record(record_ids=["r1"]) to read the content',
        'pipeshub_get_record_content(mode="content", recordId="r1") to read the content'],
      ['knowledgegraph__fetch_record(record_ids=["r1", "r2"]) to read the content',
        'pipeshub_get_record_content(mode="content", recordId=...) for each of ["r1", "r2"] to read the content'],
      ['Acme plan (block 12): knowledgegraph__fetch_record(record_ids=["r1"], start_block=9)',
        'Acme plan (block 12): pipeshub_get_record_content(mode="content", recordId="r1", startBlock=9)'],
      ['or knowledgegraph__navigate(node_id=...) to see where a record sits.',
        'or pipeshub_get_record_content(mode="navigate", nodeId=...) to see where a record sits.'],
      ['Narrow with record_types=[...], or search inside it with knowledgegraph__search(query=..., entity_ids=[...]).',
        'Narrow with record_types=[...], or search inside it with pipeshub_search(query=...).'],
      ['Search again for the rest, or use knowledgegraph__search.',
        'Search again for the rest, or use pipeshub_search.'],
      ["'currency' values are found with knowledgegraph__find_records_by_value.",
        '\'currency\' values are found with pipeshub_find_records(action="by_value").'],
      ['Continue with: knowledgegraph__find_records_by_value(amount_min=50000, currency="USD", cursor="20")',
        'Continue with: pipeshub_find_records(action="by_value", amount_min=50000, currency="USD", cursor="20")'],
      ['knowledgegraph__search_by_entities(mentions=[...], match="all") lists the records naming both',
        'pipeshub_find_records(action="by_entities", mentions=[...], match="all") lists the records naming both'],
      ['Next: knowledgegraph__find_records_by_entity(entity_id=...) lists every record of one of them',
        'Next: pipeshub_entities(action="records", entity_id=...) lists every record of one of them'],
      ['the ids knowledgegraph__search_entities returned', 'the ids pipeshub_entities(action="search") returned'],
      ['Call knowledgegraph__find_records_by_person again with the person\'s email.',
        'Call pipeshub_find_records(action="by_person") again with the person\'s email.'],
      ['knowledgegraph__find_co_mentioned_entities()', 'pipeshub_entities(action="co_mentioned")'],
    ]
    for (const [from, to] of CASES) {
      it(`rewrites ${from.slice(0, 60)}`, () => {
        expect(rewriteGuidance(from)).to.equal(to)
      })
    }

    it('leaves no internal tool name in any guidance the entity operations write', () => {
      for (const [from] of CASES) expect(rewriteGuidance(from)).to.not.match(/knowledgegraph__/)
    })

    it('leaves text without internal names alone', () => {
      const text = 'pipeshub_search_results and knowledgegraph_search are not tools here.'
      expect(rewriteGuidance(text)).to.equal(text)
    })
  })

  describe('callerEntityCapabilities', () => {
    const everything = { ...ALL_ENTITY_CAPABILITIES }

    it("asks with the caller's token and caches the answer", async () => {
      fetchStub.resolves(jsonResponse(200, everything))
      expect(await callerEntityCapabilities(DEPS, 'k1')).to.deep.equal(everything)
      expect(await callerEntityCapabilities(DEPS, 'k1')).to.deep.equal(everything)
      expect(fetchStub.callCount).to.equal(1)
      const [url, init] = fetchStub.firstCall.args
      expect(url).to.equal('http://query:8000/api/v1/search/entities/capabilities')
      expect(init.method).to.equal('GET')
      expect(init.headers.authorization).to.equal('Bearer caller-token')
    })

    it('serves nothing, and caches nothing, when the service cannot answer', async () => {
      fetchStub.onFirstCall().resolves(jsonResponse(503, { detail: 'down' }))
      fetchStub.onSecondCall().rejects(new Error('ECONNREFUSED'))
      fetchStub.onThirdCall().resolves(jsonResponse(200, everything))
      expect(await callerEntityCapabilities(DEPS, 'k2')).to.deep.equal(NO_ENTITY_CAPABILITIES)
      expect(await callerEntityCapabilities(DEPS, 'k2')).to.deep.equal(NO_ENTITY_CAPABILITIES)
      expect(await callerEntityCapabilities(DEPS, 'k2')).to.deep.equal(everything)
    })

    it('serves nothing for a refused token', async () => {
      fetchStub.resolves(jsonResponse(403, { detail: 'scope' }))
      expect(await callerEntityCapabilities(DEPS, 'k3')).to.deep.equal(NO_ENTITY_CAPABILITIES)
    })

    it('serves nothing for an answer it cannot read, rather than guess a flag', async () => {
      fetchStub.resolves(jsonResponse(200, { ...everything, recordsByEntities: 'yes' }))
      expect(await callerEntityCapabilities(DEPS, 'k4')).to.deep.equal(NO_ENTITY_CAPABILITIES)
    })

    it('asks again for another caller', async () => {
      fetchStub.onFirstCall().resolves(jsonResponse(200, NO_ENTITY_CAPABILITIES))
      fetchStub.onSecondCall().resolves(jsonResponse(200, everything))
      expect(await callerEntityCapabilities(DEPS, 'k5')).to.deep.equal(NO_ENTITY_CAPABILITIES)
      expect(await callerEntityCapabilities(DEPS, 'k6')).to.deep.equal(everything)
    })
  })
})
