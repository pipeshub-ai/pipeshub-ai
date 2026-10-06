import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { Project } from '../../../../src/modules/projects/schema/project.schema'
import { oid } from '../controller/chat-test-harness'
import { ORG } from '../helpers/conversation-world'
import { Fixture, PEOPLE, Person, TEAM_GONE, TEAM_OPS, TEAM_SALES, ORG_WIDE, asUser, defaultUsers, id, rowsOf, startFixture, team, user } from '../helpers/collab-fixture'

const PUT = (f: Fixture, who: Person, body: Record<string, unknown>) => f.http.call('PUT', f.path('chat', '/collaborators'), asUser(who), body)
const session = (f: Fixture) => f.store.session(f.ids.chat)!

describe('conversation collaboration service', () => {
  let f: Fixture
  afterEach(async () => {
    await f?.close()
    sinon.restore()
  })

  describe('invitations by editors', () => {
    const editor = async (editorsCanInvite: boolean) => {
      f = await startFixture()
      session(f).set('settings', { editorsCanInvite })
      session(f).set('sharedWith', [{ principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' }])
    }

    it('LC-07: an inviting editor adds a colleague; the row and the event name the editor', async () => {
      await editor(true)
      const out = await PUT(f, 'B', { collaborators: [user('C', 'write')] })
      expect(out.status).to.equal(200)
      const added = rowsOf(f, 'chat').find((r) => String(r.userId) === id('C'))!
      expect(added.accessLevel).to.equal('write')
      expect(String((added as { addedBy?: unknown }).addedBy)).to.equal(id('B'))
      expect(f.env.collaboration.notifier.events).to.have.length(1)
      expect(f.env.collaboration.notifier.events[0]).to.include({ type: 'chat.shared', actorUserId: id('B') })
      expect(f.env.collaboration.audit.events[0]!.actorUserId.toString()).to.equal(id('B'))
    })

    it('LC-08: without the toggle an editor is refused and nothing changes', async () => {
      await editor(false)
      const out = await PUT(f, 'B', { collaborators: [user('C', 'read')] })
      expect(out.status).to.equal(403)
      expect(out.body.error.code).to.equal('CONVERSATION_OWNER_ONLY')
      expect(rowsOf(f, 'chat')).to.have.length(1)
    })

    it('an inviting editor adding someone already there at a different level is refused, at the same level it is a no-op', async () => {
      await editor(true)
      session(f).set('sharedWith', [
        { principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' },
        { principalType: 'user', userId: PEOPLE.C, accessLevel: 'read' },
      ])
      const same = await PUT(f, 'B', { collaborators: [user('C', 'read')] })
      expect(same.status).to.equal(200)
      expect(f.env.collaboration.audit.events).to.have.length(0)
      const upgrade = await PUT(f, 'B', { collaborators: [user('C', 'write')] })
      expect(upgrade.status).to.equal(403)
    })
  })

  describe('transfer', () => {
    const shareWith = (rows: Array<Record<string, unknown>>) => {
      session(f).set('isShared', true)
      session(f).set('sharedWith', rows)
    }
    const transfer = (who: Person, to: string) => f.http.call('POST', f.path('chat', '/transfer-ownership'), asUser(who), { newOwnerUserId: to })

    it('LC-10: a viewer, a disabled user and a user of another org are all refused; the owner is unchanged', async () => {
      f = await startFixture({ collaboration: { users: { ...defaultUsers, [id('D')]: { displayName: 'Dan', isDisabled: true } } } })
      shareWith([
        { principalType: 'user', userId: PEOPLE.B, accessLevel: 'read' },
        { principalType: 'user', userId: PEOPLE.D, accessLevel: 'write' },
        { principalType: 'user', userId: PEOPLE.E, accessLevel: 'write' },
      ])
      for (const target of [id('B'), id('D'), id('E')]) {
        const out = await transfer('A', target)
        expect(out.status, target).to.equal(400)
        expect(out.body.error.code).to.equal('INVALID_PRINCIPAL')
      }
      expect(String(session(f).get('userId'))).to.equal(id('A'))
      expect(f.env.collaboration.audit.events).to.have.length(0)
    })

    it('a collaborator who is not on the chat at all is refused', async () => {
      f = await startFixture()
      const out = await transfer('A', id('C'))
      expect(out.status).to.equal(400)
      expect(out.body.error.details.principalIds).to.deep.equal([`user:${id('C')}`])
    })

    it('LC-11: on a project chat the target needs project access', async () => {
      const projectId = oid()
      f = await startFixture({
        session: { projectId, projectVisibility: 'private' },
        collaboration: { projectAccess: (userId) => userId !== id('B') },
      })
      sinon.stub(Project, 'findOne').callsFake((() =>
        Object.assign(Promise.resolve({ _id: projectId, orgId: ORG, userId: PEOPLE.A, visibility: 'private', members: [], aclVersion: 1 }), {
          lean: () => Promise.resolve({ _id: projectId, orgId: ORG, userId: PEOPLE.A, visibility: 'private', members: [], aclVersion: 1 }),
        })) as never)
      shareWith([{ principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' }])
      const out = await transfer('A', id('B'))
      expect(out.status).to.equal(403)
      expect(out.body.error.code).to.equal('PROJECT_ACCESS_REQUIRED')
      expect(String(session(f).get('userId'))).to.equal(id('A'))
    })

    it('TR-01 and NT-04: ownership moves, the old owner stays as an editor, and both are told, the email only to the new owner', async () => {
      f = await startFixture({ collaboration: { smtp: true } })
      shareWith([
        { principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' },
        { principalType: 'user', userId: PEOPLE.C, accessLevel: 'read' },
      ])
      const out = await transfer('A', id('B'))
      expect(out.status).to.equal(200)
      expect(String(session(f).get('userId'))).to.equal(id('B'))
      expect(String(session(f).get('initiator'))).to.equal(id('B'))
      const rows = rowsOf(f, 'chat')
      expect(rows.map((r) => [String(r.userId), r.accessLevel]).sort()).to.deep.equal([[id('A'), 'write'], [id('C'), 'read']].sort())
      expect((session(f).get('ownershipHistory') as unknown[]).length).to.equal(1)
      expect(f.env.collaboration.audit.events.map((e) => e.action)).to.deep.equal(['chat.ownershipTransfer'])
      expect(f.env.collaboration.notifier.events).to.have.length(1)
      expect(f.env.collaboration.notifier.events[0]).to.deep.include({
        type: 'chat.ownershipTransferred',
        newOwnerUserId: id('B'),
        previousOwnerUserId: id('A'),
        actorUserId: id('A'),
      })
      expect(f.env.collaboration.notifier.events[0]).to.have.property('emailIntent').that.deep.equals({
        template: 'chatOwnershipTransferred',
        actorName: 'Alice',
        orgName: 'Acme',
        accessLevel: 'write',
      })
      expect(out.body.owner.userId).to.equal(id('B'))
    })

    it('a second transfer by the previous owner is refused as non-owner', async () => {
      f = await startFixture()
      shareWith([{ principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' }])
      await transfer('A', id('B'))
      const again = await transfer('A', id('B'))
      expect(again.status).to.equal(403)
      expect(again.body.error.code).to.equal('CONVERSATION_OWNER_ONLY')
    })
  })

  describe('principals', () => {
    it('LC-12: a renamed team shows under its new name; the stored row has no name', async () => {
      const teams = { [TEAM_SALES]: { name: 'Sales' }, [TEAM_OPS]: { name: 'Ops' } }
      f = await startFixture({ collaboration: { teams } })
      await PUT(f, 'A', { collaborators: [team(TEAM_SALES, 'read')] })
      const before = await f.http.call('GET', f.path('chat', '/collaborators'), asUser('A'))
      expect(before.body.collaborators[0].displayName).to.equal('Sales')
      teams[TEAM_SALES].name = 'Sales EMEA'
      const after = await f.http.call('GET', f.path('chat', '/collaborators'), asUser('A'))
      expect(after.body.collaborators[0].displayName).to.equal('Sales EMEA')
      expect(Object.keys(rowsOf(f, 'chat')[0]!)).to.not.include('name')
    })

    it('LC-15: sharing with everyone needs confirmation, raises no notifications, and write needs the platform switch', async () => {
      f = await startFixture()
      const unconfirmed = await PUT(f, 'A', { collaborators: [team(ORG_WIDE, 'read')] })
      expect(unconfirmed.status).to.equal(400)
      expect(unconfirmed.body.error.code).to.equal('ORG_WIDE_CONFIRMATION_REQUIRED')
      expect(rowsOf(f, 'chat')).to.have.length(0)

      const confirmed = await PUT(f, 'A', { collaborators: [team(ORG_WIDE, 'read')], confirmOrgWide: true })
      expect(confirmed.status).to.equal(200)
      expect(rowsOf(f, 'chat')[0]!.teamId).to.equal(ORG_WIDE)
      expect(f.env.collaboration.notifier.events).to.have.length(0)
      expect(f.env.collaboration.audit.events).to.have.length(1)

      f.store.session(f.ids.chat)!.set('sharedWith', [])
      const write = await PUT(f, 'A', { collaborators: [team(ORG_WIDE, 'write')], confirmOrgWide: true })
      expect(write.status).to.equal(400)
      expect(write.body.error.details.principalIds).to.deep.equal([`team:${ORG_WIDE}`])

      f.env.collaboration.setFlag('ALLOW_ORG_WIDE_CHAT_WRITE', true)
      const allowed = await PUT(f, 'A', { collaborators: [team(ORG_WIDE, 'write')], confirmOrgWide: true })
      expect(allowed.status).to.equal(200)
    })

    it('LC-16 and LC-17: a team of another org, a team the caller is not in, and a missing team are all invalid', async () => {
      f = await startFixture()
      const foreignOrgWide = `all_${String(oid())}`
      for (const principal of [team(TEAM_OPS, 'read'), team(TEAM_GONE, 'read'), team(foreignOrgWide, 'read')]) {
        const out = await PUT(f, 'A', { collaborators: [principal], confirmOrgWide: true })
        expect(out.status, principal.principalId).to.equal(400)
        expect(out.body.error.code).to.equal('INVALID_PRINCIPAL')
      }
      expect(rowsOf(f, 'chat')).to.have.length(0)
    })

    it('a team the caller belongs to is added', async () => {
      f = await startFixture()
      const out = await PUT(f, 'A', { collaborators: [team(TEAM_SALES, 'read')] })
      expect(out.status).to.equal(200)
      expect(f.env.collaboration.notifier.events[0]).to.deep.include({ type: 'chat.shared', principal: { type: 'team', teamId: TEAM_SALES } })
    })


    it('the owner cannot be added as a collaborator', async () => {
      f = await startFixture()
      const out = await PUT(f, 'A', { collaborators: [user('A', 'read')] })
      expect(out.status).to.equal(400)
      expect(out.body.error.details.principalIds).to.deep.equal([`user:${id('A')}`])
    })

    it('service accounts and disabled users are invalid', async () => {
      f = await startFixture({ collaboration: { users: { ...defaultUsers, [id('C')]: { displayName: 'Bot', kind: 'service' }, [id('D')]: { displayName: 'Dan', isDisabled: true } } } })
      const out = await PUT(f, 'A', { collaborators: [user('C', 'read'), user('D', 'read'), user('B', 'read')] })
      expect(out.status).to.equal(400)
      expect(out.body.error.details.principalIds.sort()).to.deep.equal([`user:${id('C')}`, `user:${id('D')}`].sort())
    })
  })

  describe('limits', () => {
    it('LC-23: the 201st collaborator is a 409 with the maximum, and nothing is written', async () => {
      f = await startFixture()
      const rows = Array.from({ length: 200 }, () => ({ principalType: 'user', userId: new Types.ObjectId(), accessLevel: 'read' }))
      session(f).set('sharedWith', rows)
      const out = await PUT(f, 'A', { collaborators: [user('B', 'read')] })
      expect(out.status).to.equal(409)
      expect(out.body.error.code).to.equal('COLLABORATOR_LIMIT')
      expect(out.body.error.details).to.deep.equal({ max: 200 })
      expect(rowsOf(f, 'chat')).to.have.length(200)
    })
  })

  describe('events and audit', () => {
    it('NT-02: read to write is announced as an access change, write to read is silent', async () => {
      f = await startFixture()
      session(f).set('sharedWith', [{ principalType: 'user', userId: PEOPLE.B, accessLevel: 'read' }, { principalType: 'user', userId: PEOPLE.C, accessLevel: 'write' }])
      const out = await PUT(f, 'A', { collaborators: [user('B', 'write'), user('C', 'read')] })
      expect(out.status).to.equal(200)
      expect(f.env.collaboration.notifier.events.map((e) => [e.type, 'principal' in e && e.principal.type === 'user' ? e.principal.userId : undefined])).to.deep.equal([['chat.accessChanged', id('B')]])
      expect(f.env.collaboration.audit.events.map((e) => e.action)).to.deep.equal(['chat.accessChange', 'chat.accessChange'])
      expect(f.env.collaboration.audit.events[0]).to.deep.include({ before: { accessLevel: 'read' }, after: { accessLevel: 'write' }, aclVersion: 1 })
    })

    it('NT-03: removing someone raises no share event, only the internal unshare', async () => {
      f = await startFixture()
      session(f).set('sharedWith', [{ principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' }])
      const out = await f.http.call('DELETE', f.path('chat', `/collaborators/${id('B')}`), asUser('A'))
      expect(out.status).to.equal(200)
      expect(f.env.collaboration.notifier.events.map((e) => e.type)).to.deep.equal(['chat.unshared'])
      expect(f.env.collaboration.audit.events.map((e) => [e.action, e.before])).to.deep.equal([['chat.unshare', { accessLevel: 'write' }]])
      const again = await f.http.call('DELETE', f.path('chat', `/collaborators/${id('B')}`), asUser('A'))
      expect(again.status).to.equal(200)
      expect(f.env.collaboration.notifier.events).to.have.length(1)
    })

    it('a share to a direct user carries the email intent only when SMTP, the platform flag and the preference allow it', async () => {
      f = await startFixture({ collaboration: { smtp: true } })
      await PUT(f, 'A', { collaborators: [user('B', 'read'), team(TEAM_SALES, 'read')] })
      const [toB, toTeam] = f.env.collaboration.notifier.events
      expect(toB).to.have.property('emailIntent').that.deep.equals({ template: 'chatShared', actorName: 'Alice', orgName: 'Acme', accessLevel: 'read' })
      expect(toTeam).to.not.have.property('emailIntent')

      session(f).set('sharedWith', [])
      f.env.collaboration.notifier.batches.length = 0
      f.env.collaboration.setFlag('ENABLE_CHAT_SHARE_EMAILS', false)
      await PUT(f, 'A', { collaborators: [user('B', 'read')] })
      expect(f.env.collaboration.notifier.events[0]).to.not.have.property('emailIntent')
    })

    it('the hand-over note travels with the share event and nowhere else', async () => {
      f = await startFixture()
      await PUT(f, 'A', { collaborators: [user('B', 'read')], note: 'start with the Q3 tab' })
      expect(f.env.collaboration.notifier.events[0]).to.include({ note: 'start with the Q3 tab' })
      expect(JSON.stringify(f.env.collaboration.audit.events)).to.not.include('Q3 tab')
    })

    it('the notifier receives the actor identity and no session on a standalone server', async () => {
      f = await startFixture()
      await PUT(f, 'A', { collaborators: [user('B', 'read')] })
      const options = f.env.collaboration.notifier.options[0]!
      expect(options.session).to.equal(undefined)
      expect(options.identity?.userId).to.equal(id('A'))
    })

    it('DB-04: on a standalone server a failing notifier does not fail the share, and the audit row is kept', async () => {
      f = await startFixture()
      f.env.collaboration.notifier.failWith = new Error('outbox down')
      const out = await PUT(f, 'A', { collaborators: [user('B', 'read')] })
      expect(out.status).to.equal(200)
      expect(rowsOf(f, 'chat')).to.have.length(1)
      expect(f.env.collaboration.audit.events).to.have.length(1)
    })
  })
})
