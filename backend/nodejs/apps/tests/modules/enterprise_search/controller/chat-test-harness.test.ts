import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { InMemoryChatStore, oid } from './chat-test-harness'

describe('chat test harness: the in-memory store only keeps what was really saved', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('a conversation whose save fails is not stored, so later reads cannot find it', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const invalid = new ChatSession({ orgId: oid(), title: 'no owner' })

    let error: unknown
    try {
      await invalid.save()
    } catch (e) {
      error = e
    }

    expect((error as Error | undefined)?.name).to.equal('ValidationError')
    expect(store.sessions).to.deep.equal([])
    expect(await ChatSession.findOne({ title: 'no owner' })).to.equal(null)
  })

  it('a conversation that saves is stored and returned as saved', async () => {
    const store = new InMemoryChatStore()
    store.install()
    const valid = new ChatSession({ orgId: oid(), userId: oid(), initiator: oid(), title: 'owned' })

    expect(await valid.save()).to.equal(valid)
    expect(store.sessions).to.deep.equal([valid])
  })
})
