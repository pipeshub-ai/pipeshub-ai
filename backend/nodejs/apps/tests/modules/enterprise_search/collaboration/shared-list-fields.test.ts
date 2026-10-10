import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { Project } from '../../../../src/modules/projects/schema/project.schema'
import { listSelect, sharedListDecorator } from '../../../../src/modules/enterprise_search/services/collaboration/http/list-fields'
import { ConversationRequestContext } from '../../../../src/modules/enterprise_search/services/collaboration/http/conversation-context'
import { Fixture, PEOPLE, asUser, id, startFixture } from '../helpers/collab-fixture'
import { FakeReadState } from '../helpers/collaboration-world'

describe('shared list fields', () => {
  let f: Fixture
  afterEach(async () => {
    await f?.close()
    sinon.restore()
  })

  const share = (kind: 'chat' | 'agent', messages: number) => {
    const s = f.store.session(f.ids[kind])!
    s.set('isShared', true)
    s.set('sharedWith', [{ principalType: 'user', userId: PEOPLE.B, accessLevel: 'write' }])
    for (let i = 0; i < messages; i += 1) f.store.addMessage(s, { messageType: 'user_query', content: `m${i}` })
  }
  const stubProjects = () => sinon.stub(Project, 'find').returns({ lean: () => Promise.resolve([]) } as never)

  it('PH06-13: a recipient\'s shared-list row carries unreadCount from one read-state query; the owner also gets collaboratorCount', async () => {
    f = await startFixture()
    stubProjects()
    share('chat', 5)
    f.store.addReadState({ userId: PEOPLE.B, sessionId: f.store.session(f.ids.chat)!._id, lastReadSeq: 3 })

    const recipient = await f.http.call('GET', '/api/v1/conversations/?source=shared', asUser('B'))
    expect(recipient.status).to.equal(200)
    const row = recipient.body.conversations.find((c: { _id: string }) => c._id === f.ids.chat)
    expect(row.unreadCount).to.equal(2)
    expect(row).to.not.have.property('collaboratorCount')
    expect(row).to.not.have.property('nextSeq')
    expect(f.store.readStateQueries).to.equal(1)

    const owner = await f.http.call('GET', '/api/v1/conversations/', asUser('A'))
    const ownRow = owner.body.conversations.find((c: { _id: string }) => c._id === f.ids.chat)
    expect(ownRow).to.include({ unreadCount: 5, collaboratorCount: 1 })
    expect(ownRow).to.not.have.property('nextSeq')
  })

  it('a chat nobody else can see has no badge and no count', async () => {
    f = await startFixture()
    stubProjects()
    const solo = await f.http.call('GET', '/api/v1/conversations/', asUser('A'))
    const row = solo.body.conversations.find((c: { _id: string }) => c._id === f.ids.chat)
    expect(row).to.not.have.property('unreadCount')
    expect(row).to.not.have.property('collaboratorCount')
  })

  it('agent lists carry the fields on both the owned and the shared-with-me rows with one query', async () => {
    f = await startFixture()
    stubProjects()
    share('agent', 2)
    const recipient = await f.http.call('GET', '/api/v1/agents/agent-1/conversations', asUser('B'))
    expect(recipient.status).to.equal(200)
    expect(recipient.body.sharedWithMeConversations[0]).to.include({ unreadCount: 2 })
    expect(f.store.readStateQueries).to.equal(1)
    const owner = await f.http.call('GET', '/api/v1/agents/agent-1/conversations', asUser('A'))
    expect(owner.body.conversations[0]).to.include({ unreadCount: 2, collaboratorCount: 1 })
  })

  it('with the flag off lists are untouched and the read state is never queried', async () => {
    f = await startFixture({ collab: false })
    stubProjects()
    share('chat', 3)
    const out = await f.http.call('GET', '/api/v1/conversations/', asUser('A'))
    const row = out.body.conversations.find((c: { _id: string }) => c._id === f.ids.chat)
    expect(row).to.not.have.property('unreadCount')
    expect(f.store.readStateQueries).to.equal(0)
  })

  describe('decorator', () => {
    const ctx = (collab: boolean, userId = id('B')): ConversationRequestContext => ({ caller: { userId, orgId: id('A'), teamIds: [] }, collab })
    const row = (nextSeq: number, over: Record<string, unknown> = {}) => ({ _id: new Types.ObjectId(), userId: PEOPLE.A, sharedWith: [{}], nextSeq, ...over })

    it('never reports a negative count and treats an unopened chat as fully unread', async () => {
      const readStates = new FakeReadState()
      const opened = row(2)
      readStates.lastRead.set(`${id('B')}:${String(opened._id)}`, 9)
      const unopened = row(4)
      const decorate = await sharedListDecorator(ctx(true), [opened, unopened], readStates)
      expect(decorate({ _id: opened._id })).to.deep.equal({ _id: opened._id, unreadCount: 0 })
      expect(decorate({ _id: unopened._id })).to.deep.include({ unreadCount: 4 })
    })

    it('counts a project-visible chat without recipients as collaborative', async () => {
      const r = row(3, { sharedWith: [], projectVisibility: 'project' })
      const decorate = await sharedListDecorator(ctx(true), [r], new FakeReadState())
      expect(decorate({ _id: r._id })).to.deep.include({ unreadCount: 3 })
    })

    it('passes rows through, nextSeq included, with the flag off', async () => {
      const readStates = new FakeReadState()
      const decorate = await sharedListDecorator(ctx(false), [row(3)], readStates)
      expect(decorate({ _id: 'x', nextSeq: 3 })).to.deep.equal({ _id: 'x', nextSeq: 3 })
      expect(readStates.queries).to.equal(0)
      expect(listSelect(ctx(false))).to.equal('-__v')
      expect(listSelect(ctx(true))).to.equal('-__v +nextSeq')
    })
  })
})
