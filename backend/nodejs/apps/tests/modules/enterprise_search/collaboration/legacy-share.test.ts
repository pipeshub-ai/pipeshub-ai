import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import { IAMServiceCommand } from '../../../../src/libs/commands/iam/iam.service.command'
import { Fixture, PEOPLE, asUser, id, rowsOf, startFixture } from '../helpers/collab-fixture'

describe('legacy share and unshare', () => {
  let f: Fixture
  afterEach(async () => {
    await f?.close()
    sinon.restore()
  })

  const stubUpstreams = () => {
    sinon.stub(IAMServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as never)
    return sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {} } as never)
  }
  const share = (who: 'A' | 'B', body: Record<string, unknown>) => f.http.call('POST', f.path('chat', '/share'), asUser(who), body)
  const unshare = (body: Record<string, unknown>) => f.http.call('POST', f.path('chat', '/unshare'), asUser('A'), body)

  for (const collab of [true, false]) {
    it(`F-15: with the flag ${collab ? 'on, legacy share and unshare need conversation:share' : 'off, conversation:write still suffices (PH-01)'}`, async () => {
      f = await startFixture({ collab })
      stubUpstreams()
      const call = (path: '/share' | '/unshare', scopes: string[]) =>
        f.http.call('POST', f.path('chat', path), asUser('A', scopes), { userIds: [id('B')], ...(path === '/share' && { accessLevel: 'write' }) })
      const writeOnly = ['conversation:write']
      expect((await call('/share', writeOnly)).status).to.equal(collab ? 403 : 200)
      expect(rowsOf(f, 'chat')).to.have.length(collab ? 0 : 1)
      expect((await call('/unshare', writeOnly)).status).to.equal(collab ? 403 : 200)
      if (collab) {
        const both = [...writeOnly, 'conversation:share']
        expect((await call('/share', both)).status).to.equal(200)
        expect(rowsOf(f, 'chat').map((r) => r.accessLevel)).to.deep.equal(['write'])
        expect((await call('/unshare', both)).status).to.equal(200)
        expect(rowsOf(f, 'chat')).to.have.length(0)
      }
    })
  }

  for (const collab of [true, false]) {
    it(`SEC-17: legacy share and unshare draw on the collaboration mutate budget (flag ${collab ? 'on' : 'off'}): the 21st change is 429 RATE_LIMITED`, async () => {
      f = await startFixture({ collab })
      stubUpstreams()
      for (let i = 0; i < 10; i += 1) {
        expect((await share('A', { userIds: [id('B')] })).status, `share ${i + 1}`).to.equal(200)
        expect((await unshare({ userIds: [id('B')] })).status, `unshare ${i + 1}`).to.equal(200)
      }
      const limited = await share('A', { userIds: [id('B')] })
      expect(limited.status).to.equal(429)
      expect(limited.body.error.code).to.equal('RATE_LIMITED')
      expect(limited.body.error.details.retryAfter).to.be.a('number')
      expect((await unshare({ userIds: [id('B')] })).status).to.equal(429)
      if (collab) {
        expect((await f.http.call('PUT', f.path('chat', '/collaborators'), asUser('A'), { collaborators: [] })).status, 'the PUT shares the budget').to.equal(429)
      }
      expect((await share('B', { userIds: [id('C')] })).status, 'another user').to.not.equal(429)
    })
  }

  it('PH06-11: with the flag off a write request is stored as read and the response says so', async () => {
    f = await startFixture({ collab: false })
    stubUpstreams()
    const out = await share('A', { userIds: [id('B')], accessLevel: 'write' })
    expect(out.status).to.equal(200)
    expect(rowsOf(f, 'chat').map((r) => r.accessLevel)).to.deep.equal(['read'])
    expect(out.body.warnings).to.deep.equal([{ code: 'ACCESS_LEVEL_DOWNGRADED', requested: 'write', applied: 'read' }])
    expect(f.env.collaboration.audit.events).to.have.length(0)
    expect(f.env.collaboration.notifier.events).to.have.length(0)
  })

  it('PH06-11: with the flag on the requested level is honoured, audited and announced', async () => {
    f = await startFixture()
    stubUpstreams()
    const out = await share('A', { userIds: [id('B')], accessLevel: 'write' })
    expect(out.status).to.equal(200)
    expect(rowsOf(f, 'chat').map((r) => [String(r.userId), r.accessLevel])).to.deep.equal([[id('B'), 'write']])
    expect(out.body.warnings).to.equal(undefined)
    expect(out.body.isShared).to.equal(true)
    expect(f.env.collaboration.audit.events.map((e) => e.action)).to.deep.equal(['chat.share'])
    expect(f.env.collaboration.notifier.events.map((e) => e.type)).to.deep.equal(['chat.shared'])
  })

  it('with the flag on, an omitted level shares as read and an unknown user fails the whole request', async () => {
    f = await startFixture()
    stubUpstreams()
    const out = await share('A', { userIds: [id('B')] })
    expect(rowsOf(f, 'chat')[0]!.accessLevel).to.equal('read')
    const bad = await share('A', { userIds: [id('C'), id('E')] })
    expect(bad.status).to.equal(400)
    expect(bad.body.error.code).to.equal('INVALID_PRINCIPAL')
    expect(rowsOf(f, 'chat')).to.have.length(1)
    expect(out.status).to.equal(200)
  })

  it('unshare removes the same rows as DELETE collaborator and makes no record-permission call', async () => {
    f = await startFixture()
    const ai = stubUpstreams()
    await share('A', { userIds: [id('B'), id('C')], accessLevel: 'read' })
    ai.resetHistory()
    const out = await unshare({ userIds: [id('B')] })
    expect(out.status).to.equal(200)
    expect(out.body.unsharedUsers).to.deep.equal([id('B')])
    expect(rowsOf(f, 'chat').map((r) => String(r.userId))).to.deep.equal([id('C')])
    expect(f.env.collaboration.audit.events.map((e) => e.action)).to.deep.equal(['chat.share', 'chat.share', 'chat.unshare'])
    expect(ai.called, 'PR-7.3: the READER revoke call is gone').to.equal(false)
  })

  it('unshare of the last recipient clears isShared', async () => {
    f = await startFixture()
    stubUpstreams()
    await share('A', { userIds: [id('B')] })
    const out = await unshare({ userIds: [id('B')] })
    expect(out.body.isShared).to.equal(false)
    expect(out.body.sharedWith).to.deep.equal([])
  })

  it('a recipient cannot share or unshare', async () => {
    f = await startFixture()
    f.store.session(f.ids.chat)!.set('sharedWith', [{ principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' }])
    const out = await share('B', { userIds: [id('C')] })
    expect(out.status).to.equal(403)
  })

  it('an empty userIds list is rejected before anything runs', async () => {
    f = await startFixture({ collab: false })
    const out = await share('A', { userIds: [] })
    expect(out.status).to.equal(400)
  })
})
