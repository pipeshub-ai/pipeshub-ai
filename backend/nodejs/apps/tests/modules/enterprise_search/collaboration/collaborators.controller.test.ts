import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Fixture, Kind, PEOPLE, asUser, id, rowsOf, startFixture, user, TEAM_GONE, TEAM_OPS, TEAM_SALES } from '../helpers/collab-fixture'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { Project } from '../../../../src/modules/projects/schema/project.schema'
import { COLLABORATION_ROUTES, COLLABORATION_SCOPES, urlOf, USER_B } from '../helpers/conversation-routes'

const KINDS: Kind[] = ['chat', 'agent']

describe('collaboration routes', () => {
  let f: Fixture
  afterEach(async () => {
    await f?.close()
    sinon.restore()
  })

  for (const kind of KINDS) {
    describe(`${kind} kind`, () => {
      it('S-01: the owner shares with a colleague; the row, rev, aclVersion, audit and event follow', async () => {
        f = await startFixture()
        const out = await f.http.call('PUT', f.path(kind, '/collaborators'), asUser('A'), { collaborators: [user('B', 'write')], note: 'please review' })
        expect(out.status).to.equal(200)
        const rows = rowsOf(f, kind)
        expect(rows).to.have.length(1)
        expect(String(rows[0]!.userId)).to.equal(id('B'))
        expect(rows[0]!.accessLevel).to.equal('write')
        const session = f.store.session(f.ids[kind])!
        expect(session.get('rev')).to.equal(1)
        expect(session.get('aclVersion')).to.equal(1)
        expect(session.get('isShared')).to.equal(true)
        expect(f.env.collaboration.audit.events.map((e) => [e.action, e.principal?.principalId])).to.deep.equal([['chat.share', id('B')]])
        expect(f.env.collaboration.notifier.events).to.have.length(1)
        expect(out.body.collaborators).to.have.length(1)
        expect(out.body.collaborators[0]).to.include({ principalId: id('B'), displayName: 'Bob', accessLevel: 'write', state: 'active' })
      })

      it('S-09: one invalid principal fails the whole request; nothing is written, audited or announced', async () => {
        f = await startFixture()
        const out = await f.http.call('PUT', f.path(kind, '/collaborators'), asUser('A'), { collaborators: [user('B', 'read'), user('E', 'read')] })
        expect(out.status).to.equal(400)
        expect(out.body.error).to.include({ code: 'INVALID_PRINCIPAL' })
        expect(out.body.error.details.principalIds).to.deep.equal([`user:${id('E')}`])
        expect(rowsOf(f, kind)).to.have.length(0)
        expect(f.env.collaboration.audit.events).to.have.length(0)
        expect(f.env.collaboration.notifier.events).to.have.length(0)
      })

      it('more than 50 principals in one PUT is a validation error', async () => {
        f = await startFixture()
        const many = Array.from({ length: 51 }, () => user('B', 'read'))
        const out = await f.http.call('PUT', f.path(kind, '/collaborators'), asUser('A'), { collaborators: many })
        expect(out.status).to.equal(400)
      })

      it('a viewer cannot invite; a stranger gets 404', async () => {
        f = await startFixture()
        f.store.session(f.ids[kind])!.set('sharedWith', [{ principalType: 'user', userId: PEOPLE.C, accessLevel: 'read' }])
        const viewer = await f.http.call('PUT', f.path(kind, '/collaborators'), asUser('C'), { collaborators: [user('B', 'read')] })
        expect(viewer.status).to.equal(403)
        expect(viewer.body.error.code).to.equal('CONVERSATION_READ_ONLY')
        const stranger = await f.http.call('PUT', f.path(kind, '/collaborators'), asUser('D'), { collaborators: [user('B', 'read')] })
        expect(stranger.status).to.equal(404)
        expect(stranger.body.error.code).to.equal('CONVERSATION_NOT_FOUND')
      })

      it('TR-02: the owner cannot leave; after a transfer the previous owner can', async () => {
        f = await startFixture()
        await f.http.call('PUT', f.path(kind, '/collaborators'), asUser('A'), { collaborators: [user('B', 'write')] })
        const refused = await f.http.call('POST', f.path(kind, '/leave'), asUser('A'))
        expect(refused.status).to.equal(403)
        expect(refused.body.error.code).to.equal('CONVERSATION_OWNER_ONLY')
        const moved = await f.http.call('POST', f.path(kind, '/transfer-ownership'), asUser('A'), { newOwnerUserId: id('B') })
        expect(moved.status).to.equal(200)
        const left = await f.http.call('POST', f.path(kind, '/leave'), asUser('A'))
        expect(left.status).to.equal(200)
        expect(left.body.status).to.equal('left')
      })

      it('LC-13: a deleted team shows as such and can be removed without an existence check', async () => {
        f = await startFixture({ collaboration: { teams: { [TEAM_SALES]: { name: 'Sales' }, [TEAM_GONE]: { name: 'Old', exists: false } } } })
        f.store.session(f.ids[kind])!.set('sharedWith', [{ principalType: 'team', teamId: TEAM_GONE, accessLevel: 'read' }])
        const listed = await f.http.call('GET', f.path(kind, '/collaborators'), asUser('A'))
        expect(listed.status).to.equal(200)
        expect(listed.body.collaborators[0]).to.include({ principalType: 'team', principalId: TEAM_GONE, state: 'deleted_team' })
        const removed = await f.http.call('DELETE', f.path(kind, `/collaborators/${TEAM_GONE}?principalType=team`), asUser('A'))
        expect(removed.status).to.equal(200)
        expect(rowsOf(f, kind)).to.have.length(0)
      })

      it('SEC-14: a reader sees the owner, a count and their own access, nothing about other recipients', async () => {
        f = await startFixture()
        f.store.session(f.ids[kind])!.set('sharedWith', [{ principalType: 'team', teamId: TEAM_OPS, accessLevel: 'read' }, { principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' }])
        f.store.session(f.ids[kind])!.set('activeRun', { runId: 'run-1', userId: PEOPLE.B, startedAt: new Date(), leaseExpiresAt: new Date(Date.now() + 60000) })
        const out = await f.http.call('GET', f.path(kind, '/collaborators'), asUser('B'))
        expect(out.status).to.equal(200)
        expect(out.body).to.deep.equal({ owner: { userId: id('A'), displayName: 'Alice' }, collaboratorCount: 2, myAccess: 'write' })
        expect(JSON.stringify(out.body)).to.not.match(/sharedWith|hiddenFor|archivedFor|runId/)
      })

      it('PH06-01: team names are shown only to teams the caller belongs to', async () => {
        f = await startFixture()
        f.store.session(f.ids[kind])!.set('settings', { editorsCanInvite: true })
        f.store.session(f.ids[kind])!.set('sharedWith', [
          { principalType: 'team', teamId: TEAM_SALES, accessLevel: 'read' },
          { principalType: 'team', teamId: TEAM_OPS, accessLevel: 'read' },
          { principalType: 'user', userId: PEOPLE.C, accessLevel: 'write' },
        ])
        const ownerView = await f.http.call('GET', f.path(kind, '/collaborators'), asUser('A'))
        const names = (view: any) => Object.fromEntries(view.collaborators.map((c: any) => [c.principalId, c.displayName]))
        expect(names(ownerView.body)).to.include({ [TEAM_SALES]: 'Sales', [TEAM_OPS]: 'A team' })
        const editorView = await f.http.call('GET', f.path(kind, '/collaborators'), asUser('C'))
        expect(editorView.status).to.equal(200)
        expect(names(editorView.body)).to.include({ [TEAM_SALES]: 'A team', [TEAM_OPS]: 'A team' })
      })

      it('LC-21: leaving removes the direct row and hides the chat from the list; the team still opens it by link', async () => {
        f = await startFixture({ collaboration: { teamsOf: (userId) => (userId === id('B') ? [TEAM_SALES] : []) } })
        f.store.session(f.ids[kind])!.set('isShared', true)
        f.store.session(f.ids[kind])!.set('sharedWith', [
          { principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' },
          { principalType: 'team', teamId: TEAM_SALES, accessLevel: 'read' },
        ])
        const listedFor = async (): Promise<string[]> => {
          const out = await f.http.call('GET', kind === 'chat' ? '/api/v1/conversations/?source=shared' : '/api/v1/agents/agent-1/conversations', asUser('B'))
          expect(out.status).to.equal(200)
          const rows: Array<{ _id: string }> = kind === 'chat' ? out.body.conversations : out.body.sharedWithMeConversations
          return rows.map((r) => String(r._id))
        }
        sinon.stub(Project, 'find').returns({ lean: () => Promise.resolve([]) } as never)
        expect(await listedFor()).to.include(f.ids[kind])
        const left = await f.http.call('POST', f.path(kind, '/leave'), asUser('B'))
        expect(left.status).to.equal(200)
        expect(rowsOf(f, kind).map((r) => r.teamId ?? String(r.userId))).to.deep.equal([TEAM_SALES])
        expect(await listedFor()).to.not.include(f.ids[kind])
        const detail = await f.http.call('GET', f.path(kind), asUser('B'))
        expect(detail.status).to.equal(200)
        const readiness = await f.http.call('GET', f.path(kind, '/readiness'), asUser('B'))
        expect(readiness.body).to.deep.equal({ canSend: false, reasons: ['CONVERSATION_READ_ONLY'] })
      })

      it('an editor may not remove or demote what the owner added', async () => {
        f = await startFixture()
        f.store.session(f.ids[kind])!.set('settings', { editorsCanInvite: true })
        f.store.session(f.ids[kind])!.set('sharedWith', [
          { principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' },
          { principalType: 'user', userId: PEOPLE.C, accessLevel: 'write' },
        ])
        const removal = await f.http.call('DELETE', f.path(kind, `/collaborators/${id('C')}`), asUser('B'))
        expect(removal.status).to.equal(403)
        expect(removal.body.error.code).to.equal('CONVERSATION_OWNER_ONLY')
        const demotion = await f.http.call('PUT', f.path(kind, '/collaborators'), asUser('B'), { collaborators: [user('C', 'read')] })
        expect(demotion.status).to.equal(403)
        expect(rowsOf(f, kind).find((r) => String(r.userId) === id('C'))!.accessLevel).to.equal('write')
      })

      it('DELETE refuses an id that does not fit the principal type and writes nothing', async () => {
        f = await startFixture()
        f.store.session(f.ids[kind])!.set('sharedWith', [{ principalType: 'user', userId: PEOPLE.B, accessLevel: 'read' }])
        const bad = await f.http.call('DELETE', f.path(kind, '/collaborators/not-an-id'), asUser('A'))
        expect(bad.status).to.equal(400)
        const wrongType = await f.http.call('DELETE', f.path(kind, `/collaborators/${id('B')}?principalType=team`), asUser('A'))
        expect(wrongType.status).to.equal(400)
        const unknownType = await f.http.call('DELETE', f.path(kind, `/collaborators/${id('B')}?principalType=group`), asUser('A'))
        expect(unknownType.status).to.equal(400)
        expect(rowsOf(f, kind)).to.have.length(1)
        expect(f.env.collaboration.audit.events).to.have.length(0)
      })

      it('PATCH settings updates them for the owner only', async () => {
        f = await startFixture()
        const out = await f.http.call('PATCH', f.path(kind, '/collaboration-settings'), asUser('A'), { editorsCanInvite: true })
        expect(out.status).to.equal(200)
        expect(out.body.settings).to.deep.equal({ editorsCanInvite: true, ownerContentShared: false })
        expect(f.env.collaboration.audit.events.map((e) => e.action)).to.deep.equal(['chat.settingsChange'])
        const empty = await f.http.call('PATCH', f.path(kind, '/collaboration-settings'), asUser('A'), {})
        expect(empty.status).to.equal(400)
      })
    })
  }

  it('PH06-06: a viewer is told they are read-only; an editor lacking the agent tools sees their own missing toolsets', async () => {
    const readiness = {
      check: sinon.stub().callsFake(async (subject: { userId: string }) => (subject.userId === id('B') ? { status: 'blocked', toolsets: ['jira'] } : { status: 'ready' })),
      invalidate: sinon.stub(),
    }
    f = await startFixture({ collaboration: { readiness } })
    f.store.session(f.ids.agent)!.set('sharedWith', [
      { principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' },
      { principalType: 'user', userId: PEOPLE.C, accessLevel: 'read' },
    ])
    const viewer = await f.http.call('GET', f.path('agent', '/readiness'), asUser('C'))
    expect(viewer.body).to.deep.equal({ canSend: false, reasons: ['CONVERSATION_READ_ONLY'] })
    const editor = await f.http.call('GET', f.path('agent', '/readiness'), asUser('B'))
    expect(editor.body).to.deep.equal({ canSend: false, reasons: ['CONNECTOR_SETUP_REQUIRED'], missingToolsets: ['jira'] })
    expect(readiness.check.args.every(([subject]: [{ userId: string }]) => subject.userId === id('B'))).to.equal(true)
    const owner = await f.http.call('GET', f.path('agent', '/readiness'), asUser('A'))
    expect(owner.body).to.deep.equal({ canSend: true, reasons: [] })
  })

  it('CL-03: on an agent chat the owner archives for themself; the recipient still lists it', async () => {
    f = await startFixture()
    f.store.session(f.ids.agent)!.set('isShared', true)
    f.store.session(f.ids.agent)!.set('sharedWith', [{ principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' }])
    const archived = await f.http.call('POST', f.path('agent', '/archive'), asUser('A'))
    expect(archived.status).to.equal(200)
    const names = (rows: Array<{ _id: unknown }>) => rows.map((r) => String(r._id))
    const ownerList = await f.http.call('GET', '/api/v1/agents/agent-1/conversations', asUser('A'))
    expect(names(ownerList.body.conversations)).to.not.include(f.ids.agent)
    const recipientList = await f.http.call('GET', '/api/v1/agents/agent-1/conversations', asUser('B'))
    expect(names([...recipientList.body.conversations, ...recipientList.body.sharedWithMeConversations])).to.include(f.ids.agent)
  })

  it('SEC-17: the 21st change in a minute is 429 RATE_LIMITED with retryAfter; another user is unaffected', async () => {
    f = await startFixture()
    const put = (who: 'A' | 'B') => f.http.call('PUT', f.path('chat', '/collaborators'), asUser(who), { collaborators: [user('C', 'read')] })
    for (let i = 0; i < 20; i += 1) {
      const out = await put('A')
      expect(out.status, `request ${i + 1}`).to.equal(200)
    }
    const limited = await put('A')
    expect(limited.status).to.equal(429)
    expect(limited.body.error.code).to.equal('RATE_LIMITED')
    expect(limited.body.error.details.retryAfter).to.be.a('number')
    const other = await put('B')
    expect(other.status).to.not.equal(429)
  })

  it('SEC-17: leave shares the collab:mutate budget across chat and agent routes', async () => {
    f = await startFixture()
    const leave = (kind: 'chat' | 'agent') => f.http.call('POST', f.path(kind, '/leave'), asUser('B'))
    for (let i = 0; i < 20; i += 1) {
      const out = await leave(i % 2 === 0 ? 'chat' : 'agent')
      expect(out.status, `request ${i + 1}`).to.not.equal(429)
    }
    for (const kind of ['chat', 'agent'] as const) {
      const limited = await leave(kind)
      expect(limited.status, kind).to.equal(429)
      expect(limited.body.error.code).to.equal('RATE_LIMITED')
    }
  })

  it('PH06-12: with the flag off every collaboration route is 404 and nothing is read or written', async () => {
    f = await startFixture({ collab: false })
    const reads = ChatSession.findOne as unknown as sinon.SinonStub
    reads.resetHistory()
    for (const route of COLLABORATION_ROUTES) {
      const out = await f.http.call(route.method.toUpperCase(), urlPath(f, route), asUser('A'), route.body)
      expect(out.status, `${route.id} ${route.method} ${route.path}`).to.equal(404)
    }
    expect(reads.called).to.equal(false)
    expect(f.store.writes).to.deep.equal([])
    expect(f.env.collaboration.audit.events).to.deep.equal([])
  })

  it('PH06-02: each route demands its scope of an OAuth token and no other scope will do', async () => {
    f = await startFixture()
    for (const route of COLLABORATION_ROUTES) {
      const needed = COLLABORATION_SCOPES[route.op]!
      const wrong = ['conversation:read', 'conversation:write', 'conversation:share', 'conversation:chat'].filter((s) => s !== needed)
      const denied = await f.http.call(route.method.toUpperCase(), urlPath(f, route), asUser('A', wrong), route.body)
      expect(denied.status, `${route.id} without ${needed}`).to.equal(403)
      expect(denied.body.error.message, route.id).to.match(/Insufficient scope/)
      const allowed = await f.http.call(route.method.toUpperCase(), urlPath(f, route), asUser('A', [needed]), route.body)
      expect(allowed.body?.error?.message ?? '', `${route.id} with ${needed}`).to.not.match(/Insufficient scope/)
    }
  })
})

function urlPath(f: Fixture, route: (typeof COLLABORATION_ROUTES)[number]): string {
  const conversationId = f.ids[route.kind]
  const rel = urlOf(route, { conversationId, principalId: USER_B })
  return route.kind === 'chat' ? `/api/v1/conversations${rel}` : `/api/v1/agents${rel}`
}

