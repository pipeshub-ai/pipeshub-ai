import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { metricsBackend } from '../../../../src/libs/services/telemetry/metrics-backend'
import { Fixture, PEOPLE, asUser, id, startFixture } from '../helpers/collab-fixture'
import { useAgentProfiles } from '../../../../src/modules/enterprise_search/services/collaboration/mentions/agent.directory'

describe('conversation feed', () => {
  let f: Fixture
  afterEach(async () => {
    await f?.close()
    sinon.restore()
  })

  const chat = () => f.store.session(f.ids.chat)!
  const feed = (who: 'A' | 'B' | 'C' | 'D', query = '') => f.http.call('GET', f.path('chat', `/feed${query}`), asUser(who))
  const seed = async (opts: { messages?: number; rev?: number } = {}) => {
    f = await startFixture()
    chat().set('isShared', true)
    chat().set('sharedWith', [
      { principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' },
      { principalType: 'user', userId: PEOPLE.C, accessLevel: 'read' },
    ])
    chat().set('rev', opts.rev ?? 7)
    for (let i = 0; i < (opts.messages ?? 0); i += 1) {
      f.store.addMessage(chat(), i % 2 === 0 ? { messageType: 'user_query', content: `q${i}`, authorUserId: i % 4 === 0 ? PEOPLE.B : PEOPLE.A } : { messageType: 'bot_response', content: `a${i}`, requestedBy: PEOPLE.A })
    }
    f.store.writes.length = 0
  }

  it('PERF-05: an unchanged rev is a 304 with an empty body, one rev-only read, no message query and no read-state write', async () => {
    await seed({ messages: 3 })
    const findOne = ChatSession.findOne as unknown as sinon.SinonStub
    const messageFind = ChatSessionMessage.find as unknown as sinon.SinonStub
    findOne.resetHistory()
    messageFind.resetHistory()
    const out = await feed('B', '?afterSeq=0&rev=7')
    expect(out.status).to.equal(304)
    expect(out.body).to.equal(undefined)
    const revReads = findOne.args.filter(([, projection]: [unknown, unknown]) => JSON.stringify(projection) === JSON.stringify({ rev: 1 }))
    expect(revReads).to.have.length(1)
    expect(messageFind.called).to.equal(false)
    expect(f.env.collaboration.readState.marks).to.have.length(0)
  })

  it('PERF-02: collab_feed_poll_total counts a 200 and a 304 under their own status label', async () => {
    const polls = async (status: string): Promise<number> => {
      const m = (await metricsBackend.serialize()).match(new RegExp(`collab_feed_poll_total\\{status="${status}"\\} (\\d+)`))
      return m ? Number(m[1]) : 0
    }
    await seed({ messages: 2 })
    const before = { ok: await polls('200'), notModified: await polls('304') }
    expect((await feed('B', '?afterSeq=0&rev=3')).status).to.equal(200)
    expect((await feed('B', '?afterSeq=0&rev=7')).status).to.equal(304)
    expect((await feed('B', '?afterSeq=0&rev=7')).status).to.equal(304)
    expect((await feed('D', '?afterSeq=0&rev=7')).status).to.equal(404)
    expect(await polls('200')).to.equal(before.ok + 1)
    expect(await polls('304')).to.equal(before.notModified + 2)
  })

  it('a changed rev returns the rows, the new rev and the cursor, and records the read position', async () => {
    await seed({ messages: 4 })
    const out = await feed('B', '?afterSeq=0&rev=3')
    expect(out.status).to.equal(200)
    expect(out.body.rev).to.equal(7)
    expect(out.body.messages.map((m: { seq: number }) => m.seq)).to.deep.equal([1, 2, 3, 4])
    expect(out.body.nextSeq).to.equal(4)
    expect(out.body.hasMore).to.equal(false)
    expect(out.body.activeRun).to.equal(null)
    expect(f.env.collaboration.readState.marks).to.have.length(1)
    expect(f.env.collaboration.readState.marks[0]).to.include({ userId: id('B'), seq: 4, minIntervalMs: 10_000 })
  })

  it('a revoked caller gets 404, never a 304', async () => {
    await seed({ messages: 1 })
    const out = await feed('D', '?rev=7')
    expect(out.status).to.equal(404)
  })

  it('PH06-04: at most 100 rows per page with a cursor to the rest; authors are named; only the caller\'s feedback is returned', async () => {
    await seed({ messages: 135 })
    const doc = f.store.messages.find((m) => m.seq === 2)!
    doc.set('feedback', [
      { feedbackProvider: PEOPLE.A, isHelpful: true, metrics: { userAgent: 'a-agent' } },
      { feedbackProvider: PEOPLE.B, isHelpful: false },
    ])
    const page = await feed('B', '?afterSeq=5')
    expect(page.status).to.equal(200)
    expect(page.body.messages).to.have.length(100)
    expect(page.body.messages[0].seq).to.equal(6)
    expect(page.body.nextSeq).to.equal(105)
    expect(page.body.hasMore).to.equal(true)
    const rest = await feed('B', '?afterSeq=105')
    expect(rest.body.messages).to.have.length(30)
    expect(rest.body.hasMore).to.equal(false)

    const all = await feed('B')
    const withFeedback = all.body.messages.find((m: { seq: number }) => m.seq === 2)
    expect(withFeedback.feedback).to.have.length(1)
    expect(JSON.stringify(withFeedback.feedback)).to.not.include('a-agent')
    expect(withFeedback.feedback[0]).to.include({ isHelpful: false })
    const query = all.body.messages.find((m: { seq: number }) => m.seq === 1)
    expect(query.author).to.deep.equal({ userId: id('B'), displayName: 'Bob' })
    const answer = all.body.messages.find((m: { seq: number }) => m.seq === 2)
    expect(answer.author).to.deep.equal({ userId: id('A'), displayName: 'Alice' })
    expect(answer).to.not.have.property('sessionId')
    expect(answer).to.not.have.property('orgId')
  })

  it('a note row is authored by its writer (the owner when none is stored) and keeps its mentions', async () => {
    await seed({ messages: 0 })
    f.store.addMessage(chat(), { messageType: 'note', content: 'n1', authorUserId: PEOPLE.B, mentions: [{ kind: 'user', id: PEOPLE.A }] })
    f.store.addMessage(chat(), { messageType: 'note', content: 'n2' })
    const rows = (await feed('A')).body.messages
    expect(rows.map((m: { messageType: string }) => m.messageType)).to.deep.equal(['note', 'note'])
    expect(rows[0].author).to.deep.equal({ userId: id('B'), displayName: 'Bob' })
    expect(rows[0].mentions).to.have.length(1)
    expect(rows[1].author).to.deep.equal({ userId: id('A'), displayName: 'Alice' })
  })

  it('the active run shows its runner to everyone and its runId only to people who can write', async () => {
    await seed({ messages: 1 })
    chat().set('activeRun', { runId: 'run-9', userId: PEOPLE.B, startedAt: new Date('2026-10-02T10:00:00Z'), leaseExpiresAt: new Date(Date.now() + 60_000) })
    const reader = await feed('C')
    expect(reader.body.activeRun).to.deep.equal({ userId: id('B'), displayName: 'Bob', startedAt: '2026-10-02T10:00:00.000Z' })
    const editor = await feed('B')
    expect(editor.body.activeRun.runId).to.equal('run-9')
    const owner = await feed('A')
    expect(owner.body.activeRun.runId).to.equal('run-9')
  })

  it('PH06-05: two polls in ten seconds write the read position once; a later poll cannot lower it', async () => {
    await seed({ messages: 3 })
    await feed('B')
    await feed('B', '?rev=1')
    expect(f.env.collaboration.readState.marks).to.have.length(1)
    await feed('C')
    expect(f.env.collaboration.readState.marks).to.have.length(2)
    expect(f.env.collaboration.readState.lastRead.get(`${id('B')}:${f.ids.chat}`)).to.equal(3)
  })

  it('a poll that returns nothing new does not move the read position', async () => {
    await seed({ messages: 2 })
    const out = await feed('B', '?afterSeq=2&rev=1')
    expect(out.status).to.equal(200)
    expect(out.body.messages).to.deep.equal([])
    expect(out.body.nextSeq).to.equal(2)
    expect(f.env.collaboration.readState.marks).to.have.length(0)
  })

  it('a failing read-state write never fails the poll', async () => {
    await seed({ messages: 2 })
    f.env.collaboration.readState.markRead = async () => {
      throw new Error('mongo down')
    }
    const out = await feed('B')
    expect(out.status).to.equal(200)
  })

  it('the feed of an agent conversation works through the agent router', async () => {
    await seed()
    f.store.session(f.ids.agent)!.set('rev', 2)
    const out = await f.http.call('GET', f.path('agent', '/feed?rev=2'), asUser('A'))
    expect(out.status).to.equal(304)
  })

  it('rejects a malformed cursor', async () => {
    await seed()
    const out = await feed('B', '?afterSeq=abc')
    expect(out.status).to.equal(400)
  })

  describe('aclVersion', () => {
    it('is in the 200 body and in the X-Acl-Version header, and on the 304 too', async () => {
      await seed({ messages: 2 })
      chat().set('aclVersion', 5)
      const ok = await feed('B', '?afterSeq=0&rev=3')
      expect(ok.status).to.equal(200)
      expect(ok.body.aclVersion).to.equal(5)
      expect(ok.headers.get('x-acl-version')).to.equal('5')
      const notModified = await feed('B', '?afterSeq=0&rev=7')
      expect(notModified.status).to.equal(304)
      expect(notModified.headers.get('x-acl-version')).to.equal('5')
    })

    it('a chat that never changed its sharing reads as 0', async () => {
      await seed({ messages: 1 })
      expect((await feed('B', '?rev=7')).headers.get('x-acl-version')).to.equal('0')
    })

    it('a sharing change moves the header on the next poll even though rev did not move, so it cannot hide behind a 304', async () => {
      await seed({ messages: 1 })
      const before = await feed('B', '?rev=7')
      expect(before.status).to.equal(304)
      chat().set('aclVersion', (chat().get('aclVersion') ?? 0) + 1)
      const after = await feed('B', '?rev=7')
      expect(after.status).to.equal(304)
      expect(Number(after.headers.get('x-acl-version'))).to.equal(Number(before.headers.get('x-acl-version')) + 1)
    })

    it('adds no read: the 304 poll still makes the guard’s read and the rev read, and no message query', async () => {
      await seed({ messages: 2 })
      const findOne = ChatSession.findOne as unknown as sinon.SinonStub
      const messageFind = ChatSessionMessage.find as unknown as sinon.SinonStub
      findOne.resetHistory()
      messageFind.resetHistory()
      await feed('B', '?afterSeq=0&rev=7')
      expect(findOne.callCount).to.equal(2)
      expect(messageFind.called).to.equal(false)
    })
  })

  describe('respondingAgent (M2)', () => {
    const profiles = { describe: async (_who: unknown, key: string) => (key === 'guest-9' ? { name: 'Joke Buddy', handle: 'joke-buddy' } : undefined) }
    const open = async () => {
      f = await startFixture({ collaboration: { agentProfiles: profiles as never } })
      chat().set('sharedWith', [{ principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' }])
      chat().set('isShared', true)
      f.store.addMessage(chat(), { messageType: 'user_query', content: 'q', authorUserId: PEOPLE.A })
      f.store.addMessage(chat(), { messageType: 'bot_response', content: 'knock knock', requestedBy: PEOPLE.A, respondingAgentKey: 'guest-9' })
      f.store.addMessage(chat(), { messageType: 'bot_response', content: 'secret', requestedBy: PEOPLE.A, respondingAgentKey: 'private-agent' })
      f.store.addMessage(chat(), { messageType: 'bot_response', content: 'plain', requestedBy: PEOPLE.A })
    }
    afterEach(() => useAgentProfiles(undefined))

    it('the feed names a guest agent’s answer by key, name and handle', async () => {
      await open()
      const rows = (await feed('B', '?afterSeq=0')).body.messages
      expect(rows[1].respondingAgent).to.deep.equal({ key: 'guest-9', name: 'Joke Buddy', handle: 'joke-buddy' })
      expect(rows[1].respondingAgentKey).to.equal('guest-9')
    })

    it('an agent the caller cannot read is just its key, and nothing else carries the field', async () => {
      await open()
      const rows = (await feed('B', '?afterSeq=0')).body.messages
      expect(rows[2].respondingAgent).to.deep.equal({ key: 'private-agent' })
      expect(rows[0].respondingAgent).to.equal(undefined)
      expect(rows[3].respondingAgent).to.equal(undefined)
    })

    it('the conversation detail returns it too', async () => {
      await open()
      useAgentProfiles(profiles as never)
      const out = await f.http.call('GET', f.path('chat'), asUser('B'))
      expect(out.status, JSON.stringify(out.body)).to.equal(200)
      const guest = out.body.conversation.messages.find((m: any) => m.content === 'knock knock')
      expect(guest.respondingAgent).to.deep.equal({ key: 'guest-9', name: 'Joke Buddy', handle: 'joke-buddy' })
      expect(out.body.conversation.messages.find((m: any) => m.content === 'secret').respondingAgent).to.deep.equal({ key: 'private-agent' })
      expect(out.body.conversation.messages.find((m: any) => m.content === 'plain').respondingAgent).to.equal(undefined)
    })
  })
})
