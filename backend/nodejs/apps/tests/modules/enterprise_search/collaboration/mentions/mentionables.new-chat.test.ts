import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { Fixture, OTHER_ORG, asUser, id, startFixture } from '../../helpers/collab-fixture'

const newId = (): string => String(new Types.ObjectId())
const JOHN = newId()
const FOREIGN = newId()
const OFF = newId()
const users = {
  [id('A')]: { displayName: 'Alice Anderson', firstName: 'Alice', lastName: 'Anderson', email: 'alice@acme.test' },
  [id('B')]: { displayName: 'Bob Brown', firstName: 'Bob', lastName: 'Brown', email: 'bob@acme.test' },
  [JOHN]: { displayName: 'John Smith', firstName: 'John', middleName: 'Michael', lastName: 'Smith', email: 'john.smith@acme.test' },
  [OFF]: { displayName: 'John Disabled', firstName: 'John', lastName: 'Disabled', isDisabled: true },
  [FOREIGN]: { displayName: 'John Foreign', firstName: 'John', lastName: 'Foreign', orgId: OTHER_ORG },
}
const agentList = [
  { agentKey: 'agent-1', name: 'Offer drafter', handle: 'offer-drafter', isServiceAccount: false },
  { agentKey: 'sa-1', name: 'Robot', handle: 'robot', isServiceAccount: true },
]
const agents = { canExecute: async () => true, isServiceAccount: async (_who: unknown, key: string) => key === 'sa-1' }
const profiles = { describe: async () => undefined }

describe('GET /conversations/mentionables: the picker before the chat exists', () => {
  let f: Fixture
  afterEach(async () => {
    await f?.close()
    sinon.restore()
  })
  const open = async (over: Record<string, unknown> = {}) => {
    f = await startFixture({ collaboration: { mentions: true, agentBuilder: true, users, agents, agentProfiles: profiles, agentList, ...over } as never })
  }
  const get = (query = '', who: 'A' | 'B' = 'A', base = '/api/v1/conversations') => f.http.call('GET', `${base}/mentionables${query}`, asUser(who))
  const labels = (body: any): string[] => body.items.map((i: any) => `${i.type}:${i.label}`)

  it('offers the assistant, the agents the caller can run and, for typed text, org members who are not in the chat', async () => {
    await open()
    const out = await get('?q=john')
    expect(out.status).to.equal(200)
    expect(labels(out.body)).to.deep.equal(['user:John Smith'])
    expect(out.body.items[0]).to.deep.equal({ type: 'user', id: JOHN, label: 'John Smith', inChat: false, email: 'john.smith@acme.test' })
    expect(labels((await get()).body)).to.deep.equal(['assistant:PipesHub', 'agent:Offer drafter', 'agent:Robot'])
  })

  it('is not taken for a conversation id: the route is not shadowed by /:conversationId', async () => {
    await open()
    expect((await get('')).status).to.equal(200)
  })

  it('include marks draft collaborators as in the chat, even without a typed query, and they still honour the query', async () => {
    await open()
    expect(labels((await get(`?include=${JOHN}`)).body)).to.include('user:John Smith')
    const withQ = (await get(`?q=smi&include=${JOHN},${id('B')}`)).body.items.filter((i: any) => i.type === 'user')
    expect(withQ).to.deep.equal([{ type: 'user', id: JOHN, label: 'John Smith', inChat: true, email: 'john.smith@acme.test' }])
    // A draft collaborator who does not match is left out; a match outside the draft is offered as not in the chat.
    expect((await get(`?q=bro&include=${JOHN}`)).body.items.filter((i: any) => i.type === 'user').map((i: any) => [i.label, i.inChat])).to.deep.equal([['Bob Brown', false]])
  })

  it('never offers the caller, a disabled user or another org’s user, even when included', async () => {
    await open()
    const out = await get(`?q=john&include=${OFF},${FOREIGN},${id('A')}`)
    expect(labels(out.body)).to.deep.equal(['user:John Smith'])
    expect(out.body.items[0].inChat).to.equal(false)
  })

  it('a service-account agent drops out once a collaborator is drafted, as it would in a shared chat', async () => {
    await open()
    expect(labels((await get()).body)).to.include('agent:Robot')
    expect(labels((await get(`?include=${JOHN}`)).body)).to.not.include('agent:Robot')
  })

  it('an agent chat’s picker puts its own agent first and takes the agent key from the path', async () => {
    await open()
    const out = await get('', 'A', '/api/v1/agents/agent-1/conversations')
    expect(out.status).to.equal(200)
    expect(labels(out.body)).to.deep.equal(['assistant:PipesHub', 'agent:Offer drafter', 'agent:Robot'])
  })

  it('caps the list and rejects a malformed include', async () => {
    await open()
    expect((await get('?limit=1&q=john')).body.items).to.have.length(1)
    expect((await get('?include=nope')).status).to.equal(400)
    expect((await get('?limit=21')).status).to.equal(400)
  })

  it('is absent with mentions off', async () => {
    await open({ mentions: false })
    expect((await get()).status).to.equal(404)
  })

  it('is absent with collaborative chats off', async () => {
    f = await startFixture({ collab: false, collaboration: { mentions: true, users } })
    expect((await get()).status).to.equal(404)
  })
})
