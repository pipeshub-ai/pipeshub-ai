import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { MongoConversationMessageFeed } from '../../../../../../src/modules/enterprise_search/services/collaboration/persistence/message-feed'
import { InMemoryChatStore, oid } from '../../../controller/chat-test-harness'

describe('MongoConversationMessageFeed.historyBefore', () => {
  afterEach(() => sinon.restore())

  const world = () => {
    const store = new InMemoryChatStore()
    store.install()
    const session = store.addSession({ orgId: oid(), userId: oid(), initiator: oid() })
    const other = store.addSession({ orgId: session.orgId, userId: oid(), initiator: oid() })
    return { store, session, other }
  }

  it('LS-08: returns the rows below seq in order, leaving out the question itself and anything after it', async () => {
    const { store, session, other } = world()
    for (const content of ['q0', 'a0', 'q1', 'a1']) store.addMessage(session, { messageType: 'user_query', content })
    store.addMessage(other, { messageType: 'user_query', content: 'another chat' })

    const history = await new MongoConversationMessageFeed().historyBefore(session._id, 3)

    expect(history.map((m) => m.content)).to.deep.equal(['q0', 'a0'])
  })

  it('is empty before the first row, and reads inside the given transaction session', async () => {
    const { store, session } = world()
    store.addMessage(session, { messageType: 'user_query', content: 'q0' })
    const dbSession = {} as never

    expect(await new MongoConversationMessageFeed().historyBefore(session._id, 1, { dbSession })).to.deep.equal([])
  })
})
