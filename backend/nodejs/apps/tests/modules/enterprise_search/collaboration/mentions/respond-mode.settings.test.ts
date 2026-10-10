import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Fixture, Kind, asUser, id, startFixture, user } from '../../helpers/collab-fixture'

const KINDS: Kind[] = ['chat', 'agent']

describe('settings.respondMode on PATCH .../collaboration-settings (PR-10.4)', () => {
  let f: Fixture
  afterEach(async () => {
    await f?.close()
    sinon.restore()
  })

  for (const kind of KINDS) {
    describe(kind, () => {
      const patch = (who: 'A' | 'B' | 'C', body: Record<string, unknown>) => f.http.call('PATCH', f.path(kind, '/collaboration-settings'), asUser(who), body)

      it('the owner sets each mode; it is stored, audited and visible in the settings view', async () => {
        f = await startFixture()
        for (const mode of ['mention_only', 'always', 'smart']) {
          const out = await patch('A', { respondMode: mode })
          expect(out.status, mode).to.equal(200)
          expect(out.body.settings).to.deep.equal({ editorsCanInvite: false, ownerContentShared: false, respondMode: mode })
          expect(f.store.session(f.ids[kind])!.get('settings.respondMode')).to.equal(mode)
        }
        const audit = f.env.collaboration.audit.events
        expect(audit.map((e) => e.action)).to.deep.equal(['chat.settingsChange', 'chat.settingsChange', 'chat.settingsChange'])
        expect(audit[0]!.after).to.deep.include({ respondMode: 'mention_only' })
        expect(f.store.session(f.ids[kind])!.get('rev')).to.equal(3)
      })

      it('it combines with the other settings, and changes only what it names', async () => {
        f = await startFixture()
        await patch('A', { respondMode: 'always' })
        const out = await patch('A', { editorsCanInvite: true })
        expect(out.body.settings).to.deep.equal({ editorsCanInvite: true, ownerContentShared: false, respondMode: 'always' })
      })

      it('a chat that never set it keeps the settings view exactly as before (absent reads as smart)', async () => {
        f = await startFixture()
        const out = await f.http.call('GET', f.path(kind, '/collaborators'), asUser('A'))
        expect(out.body.settings).to.deep.equal({ editorsCanInvite: false, ownerContentShared: false })
        expect(f.store.session(f.ids[kind])!.get('settings.respondMode')).to.equal(undefined)
      })

      it('only the owner may set it: an editor gets 403 CONVERSATION_OWNER_ONLY, nothing changes', async () => {
        f = await startFixture()
        await f.http.call('PUT', f.path(kind, '/collaborators'), asUser('A'), { collaborators: [user('B', 'write')] })
        const out = await patch('B', { respondMode: 'always' })
        expect(out.status).to.equal(403)
        expect(out.body.error.code).to.equal('CONVERSATION_OWNER_ONLY')
        expect(f.store.session(f.ids[kind])!.get('settings.respondMode')).to.equal(undefined)
      })

      it('an unknown mode is a 400 and nothing is written', async () => {
        f = await startFixture()
        for (const bad of ['loud', '', 1, null]) {
          expect((await patch('A', { respondMode: bad })).status, String(bad)).to.equal(400)
        }
        expect(f.env.collaboration.audit.events).to.have.length(0)
        expect(id('A')).to.be.a('string')
      })
    })
  }

  it('the route stays unmounted with collaborative chats off', async () => {
    f = await startFixture({ collab: false })
    expect((await f.http.call('PATCH', f.path('chat', '/collaboration-settings'), asUser('A'), { respondMode: 'always' })).status).to.equal(404)
  })
})
