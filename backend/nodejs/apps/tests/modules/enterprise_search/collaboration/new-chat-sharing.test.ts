import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { COLLAB_TYPES } from '../../../../src/modules/enterprise_search/services/collaboration/collab.types'
import { INewChatSharing } from '../../../../src/modules/enterprise_search/services/collaboration/conversation-collaboration.service'
import { Fixture, ORG_WIDE, PEOPLE, TEAM_SALES, id, rowsOf, startFixture } from '../helpers/collab-fixture'
import { ORG } from '../helpers/conversation-world'

describe('sharing a chat as it is created (first-send share)', () => {
  let f: Fixture
  afterEach(async () => {
    await f?.close()
    sinon.restore()
  })
  const owner = { userId: id('A'), orgId: String(ORG), teamIds: 'unresolved' as const }
  const identity = { userId: id('A'), orgId: String(ORG), authHeaders: {}, requestKey: {} }
  const sharing = (): INewChatSharing => f.env.container.get<INewChatSharing>(COLLAB_TYPES.NewChatSharing)
  const user = (who: 'B' | 'C' | 'D' | 'E', accessLevel: 'read' | 'write') => ({ principal: { type: 'user' as const, userId: id(who) }, accessLevel })
  const rejects = async (p: Promise<unknown>): Promise<any> => {
    try {
      await p
    } catch (e) {
      return e
    }
    throw new Error('expected a rejection')
  }

  describe('validate (before anything is created)', () => {
    it('accepts what the collaborators route would accept from an owner and writes nothing', async () => {
      f = await startFixture()
      await sharing().validate(owner, identity, { collaborators: [user('B', 'write'), user('C', 'read')] })
      expect(rowsOf(f, 'chat')).to.have.length(0)
      expect(f.env.collaboration.audit.events).to.have.length(0)
      expect(f.env.collaboration.notifier.events).to.have.length(0)
    })

    it('refuses another org’s user, a disabled user and the owner themself', async () => {
      f = await startFixture({ collaboration: { users: { [id('A')]: { displayName: 'Alice' }, [id('B')]: { displayName: 'Bob', isDisabled: true }, [id('C')]: { displayName: 'Carol' } } } })
      for (const who of ['B', 'E'] as const) {
        const e = await rejects(sharing().validate(owner, identity, { collaborators: [user(who, 'read')] }))
        expect(e.code, who).to.equal('INVALID_PRINCIPAL')
      }
      const self = await rejects(sharing().validate(owner, identity, { collaborators: [{ principal: { type: 'user', userId: id('A') }, accessLevel: 'read' }] }))
      expect(self.code).to.equal('INVALID_PRINCIPAL')
    })

    it('refuses an org-wide write when the platform policy is off, and an org-wide share without confirmation', async () => {
      f = await startFixture()
      const everyone = (accessLevel: 'read' | 'write') => ({ principal: { type: 'team' as const, teamId: ORG_WIDE }, accessLevel })
      const write = await rejects(sharing().validate(owner, identity, { collaborators: [everyone('write')], confirmOrgWide: true }))
      expect(write.statusCode).to.be.within(400, 403)
      const unconfirmed = await rejects(sharing().validate(owner, identity, { collaborators: [everyone('read')] }))
      expect(unconfirmed.statusCode).to.be.within(400, 409)
      await sharing().validate(owner, identity, { collaborators: [everyone('read')], confirmOrgWide: true })
    })

    it('is a 404, as the collaborators routes are, with collaborative chats off', async () => {
      f = await startFixture({ collab: false })
      const e = await rejects(sharing().validate(owner, identity, { collaborators: [user('B', 'read')] }))
      expect(e.statusCode).to.equal(404)
    })
  })

  describe('apply (right after the chat and its first row exist)', () => {
    it('adds the collaborators once, with the audit rows and one chat.shared event each, carrying the note', async () => {
      f = await startFixture()
      await sharing().apply(owner, identity, { id: f.ids.chat, kind: 'chat' }, { collaborators: [user('B', 'write'), user('C', 'read')], note: 'Take a look' })
      const rows = rowsOf(f, 'chat')
      expect(rows.map((r) => [String(r.userId), r.accessLevel])).to.have.deep.members([
        [id('B'), 'write'],
        [id('C'), 'read'],
      ])
      expect(f.env.collaboration.audit.events).to.have.length(2)
      const shared = f.env.collaboration.notifier.events.filter((e: any) => e.type === 'chat.shared')
      expect(shared).to.have.length(2)
      expect(shared.every((e: any) => e.note === 'Take a look' && e.actorUserId === id('A'))).to.equal(true)
    })

    it('a team principal the owner belongs to is added', async () => {
      f = await startFixture()
      await sharing().apply(owner, identity, { id: f.ids.chat, kind: 'chat' }, { collaborators: [{ principal: { type: 'team', teamId: TEAM_SALES }, accessLevel: 'read' }] })
      expect(rowsOf(f, 'chat').map((r) => r.teamId)).to.deep.equal([TEAM_SALES])
    })

    it('works for an agent chat too', async () => {
      f = await startFixture()
      await sharing().apply(owner, identity, { id: f.ids.agent, kind: 'agent', agentKey: 'agent-1' }, { collaborators: [user('B', 'read')] })
      expect(rowsOf(f, 'agent')).to.have.length(1)
    })

    it('refuses what validation would have refused, so the caller can roll the chat back', async () => {
      f = await startFixture()
      const e = await rejects(sharing().apply(owner, identity, { id: f.ids.chat, kind: 'chat' }, { collaborators: [user('E', 'read')] }))
      expect(e.code).to.equal('INVALID_PRINCIPAL')
      expect(rowsOf(f, 'chat')).to.have.length(0)
      expect(f.env.collaboration.notifier.events).to.have.length(0)
      void PEOPLE
    })
  })
})
