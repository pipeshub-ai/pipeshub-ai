import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Fixture, Kind, PEOPLE, TEAM_OPS, TEAM_SALES, asUser, id, startFixture, user, team } from '../../helpers/collab-fixture'

const KINDS: Kind[] = ['chat', 'agent']

describe('GET .../mentionables (PH10-11)', () => {
  let f: Fixture
  afterEach(async () => {
    await f?.close()
    sinon.restore()
  })

  const share = (kind: Kind, rows: unknown[]) => f.http.call('PUT', f.path(kind, '/collaborators'), asUser('A'), { collaborators: rows })
  const list = (kind: Kind, who: 'A' | 'B' | 'C' | 'D' | 'E', query = '') => f.http.call('GET', f.path(kind, `/mentionables${query}`), asUser(who))
  const labels = (body: any): string[] => body.items.map((i: any) => `${i.type}:${i.label}`)

  for (const kind of KINDS) {
    describe(kind, () => {
      it('offers an inviting editor the assistant, the other people of the chat and its teams, never the caller', async () => {
        f = await startFixture({ collaboration: { mentions: true } })
        await share(kind, [user('B', 'write'), user('C', 'read'), team(TEAM_SALES, 'write')])
        expect((await f.http.call('PATCH', f.path(kind, '/collaboration-settings'), asUser('A'), { editorsCanInvite: true })).status).to.equal(200)
        const out = await list(kind, 'B')
        expect(out.status).to.equal(200)
        expect(labels(out.body)).to.deep.equal(['assistant:PipesHub', 'user:Alice', 'user:Carol', 'team:Sales'])
        expect(out.body.items[0]).to.deep.equal({ type: 'assistant', id: 'self', label: 'PipesHub' })
        expect(out.body.items[1].id).to.equal(id('A'))
      })

      it('I-9: a reader or a non-inviting editor gets the assistant and the owner, not who else is in the chat', async () => {
        f = await startFixture({ collaboration: { mentions: true } })
        await share(kind, [user('B', 'write'), user('C', 'read'), team(TEAM_SALES, 'write')])
        for (const who of ['B', 'C'] as const) {
          const out = await list(kind, who)
          expect(out.status, who).to.equal(200)
          expect(labels(out.body), who).to.deep.equal(['assistant:PipesHub', 'user:Alice'])
          expect(JSON.stringify(out.body), who).to.not.contain('Sales').and.not.contain(TEAM_SALES)
        }
      })

      it('filters by prefix of any word and caps at the limit', async () => {
        f = await startFixture({ collaboration: { mentions: true } })
        await share(kind, [user('B', 'write'), user('C', 'read'), team(TEAM_SALES, 'write')])
        expect(labels((await list(kind, 'A', '?q=ca')).body)).to.deep.equal(['user:Carol'])
        expect(labels((await list(kind, 'A', '?q=PIPES')).body)).to.deep.equal(['assistant:PipesHub'])
        expect(labels((await list(kind, 'A', '?q=sal')).body)).to.deep.equal(['team:Sales'])
        expect((await list(kind, 'A', '?limit=2')).body.items).to.have.length(2)
        expect((await list(kind, 'A', '?limit=21')).status).to.equal(400)
        expect((await list(kind, 'A', '?limit=0')).status).to.equal(400)
      })

      it('lists only this chat’s own people and teams: a team of another chat or a user outside it never appears', async () => {
        f = await startFixture({ collaboration: { mentions: true } })
        await share(kind, [user('B', 'write')])
        const out = await list(kind, 'A')
        expect(labels(out.body)).to.deep.equal(['assistant:PipesHub', 'user:Bob'])
        expect(JSON.stringify(out.body)).to.not.contain(TEAM_OPS).and.not.contain(id('C')).and.not.contain(id('D'))
      })

      it('drops participants the directory no longer knows (deleted, other org) and teams that were deleted', async () => {
        f = await startFixture({ collaboration: { mentions: true, teams: { [TEAM_SALES]: { name: 'Sales', exists: false } } } })
        f.store.session(f.ids[kind])!.set('sharedWith', [
          { principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' },
          { principalType: 'user', userId: PEOPLE.E, accessLevel: 'read' },
          { principalType: 'team', teamId: TEAM_SALES, accessLevel: 'read' },
        ])
        expect(labels((await list(kind, 'A')).body)).to.deep.equal(['assistant:PipesHub', 'user:Bob'])
      })

      it('a stranger gets 404 and a read-only participant may list', async () => {
        f = await startFixture({ collaboration: { mentions: true } })
        await share(kind, [user('C', 'read')])
        expect((await list(kind, 'D')).status).to.equal(404)
        expect((await list(kind, 'E')).status).to.equal(404)
        expect((await list(kind, 'C')).status).to.equal(200)
      })

      it('the route is absent with mentions off', async () => {
        f = await startFixture({ collaboration: { mentions: false } })
        expect((await list(kind, 'A')).status).to.equal(404)
      })
    })
  }

  describe('agents (AB-11, #16a)', () => {
    const agents = {
      canExecute: async (who: { userId: string }, key: string) => key === 'agent-1' && [id('A'), id('B')].includes(who.userId),
      isServiceAccount: async () => false,
    }
    const agentProfiles = {
      describe: async (_who: unknown, key: string) => (key === 'agent-1' ? { name: 'Offer drafter', handle: 'offer-drafter' } : undefined),
    }
    const agentList = [{ agentKey: 'agent-1', name: 'Offer drafter', handle: 'offer-drafter', isServiceAccount: false }]
    const open = (agentBuilder = true) => startFixture({ collaboration: { mentions: true, agentBuilder, agents, agentProfiles, agentList } })

    it('M2: a default chat offers the agents the caller can run, since the validator accepts any of them', async () => {
      f = await open()
      expect(labels((await list('chat', 'A')).body)).to.deep.equal(['assistant:PipesHub', 'agent:Offer drafter'])
    })

    it('M2: an agent chat also offers other agents the caller can run, its own first', async () => {
      const two = [...agentList, { agentKey: 'agent-2', name: 'Alpha', handle: 'alpha', isServiceAccount: false }]
      f = await startFixture({
        collaboration: { mentions: true, agentBuilder: true, agents: { ...agents, canExecute: async () => true }, agentProfiles, agentList: two },
      })
      expect(labels((await list('agent', 'A')).body)).to.deep.equal(['assistant:PipesHub', 'agent:Offer drafter', 'agent:Alpha'])
    })

    it('M2: offers at most 20 agents', async () => {
      const many = Array.from({ length: 30 }, (_, i) => ({ agentKey: `a${i}`, name: `Agent ${i}`, handle: `agent-${i}`, isServiceAccount: false }))
      f = await startFixture({
        collaboration: { mentions: true, agentBuilder: true, agents: { ...agents, canExecute: async () => true }, agentProfiles, agentList: many },
      })
      expect((await list('chat', 'A', '?limit=20')).body.items.filter((i: any) => i.type === 'agent').length).to.be.at.most(20)
    })

    it('M2: an agent list that is down leaves the rest of the picker intact', async () => {
      f = await startFixture({ collaboration: { mentions: true, agentBuilder: true, agents, agentProfiles, agentList: 'unavailable' } })
      expect(labels((await list('chat', 'A')).body)).to.deep.equal(['assistant:PipesHub'])
    })

    it('a shared agent chat offers its agent to every participant who can run it, creator or not', async () => {
      f = await open()
      await share('agent', [user('B', 'write'), user('C', 'write')])
      const want = { type: 'agent', id: 'agent-1', label: 'Offer drafter', handle: 'offer-drafter' }
      expect((await list('agent', 'A')).body.items).to.deep.include(want)
      expect((await list('agent', 'B')).body.items).to.deep.include(want)
      expect(JSON.stringify((await list('agent', 'C')).body)).to.not.contain('agent-1').and.not.contain('Offer drafter')
    })

    it('matches the name by word prefix and the handle by prefix', async () => {
      f = await open()
      expect(labels((await list('agent', 'A', '?q=off')).body)).to.deep.equal(['agent:Offer drafter'])
      expect(labels((await list('agent', 'A', '?q=offer-d')).body)).to.deep.equal(['agent:Offer drafter'])
      expect(labels((await list('agent', 'A', '?q=@offer')).body)).to.deep.equal(['agent:Offer drafter'])
      expect(labels((await list('agent', 'A', '?q=zzz')).body)).to.deep.equal([])
    })

    it('stays out of the list with the agent builder off', async () => {
      f = await open(false)
      expect(labels((await list('agent', 'A')).body)).to.not.include('agent:Offer drafter')
    })

    it('a service-account agent is not offered in a shared chat', async () => {
      f = await startFixture({
        collaboration: { mentions: true, agentBuilder: true, agents: { ...agents, isServiceAccount: async () => true }, agentProfiles, agentList },
      })
      await share('agent', [user('B', 'write')])
      expect(labels((await list('agent', 'A')).body)).to.deep.equal(['assistant:PipesHub', 'user:Bob'])
    })

    it('a directory that is down leaves the rest of the picker intact', async () => {
      f = await startFixture({
        collaboration: { mentions: true, agentBuilder: true, agents: { canExecute: async () => 'unavailable' as const, isServiceAccount: async () => false }, agentProfiles, agentList },
      })
      expect(labels((await list('agent', 'A')).body)).to.deep.equal(['assistant:PipesHub'])
    })
  })
})
