import { expect } from 'chai'
import sinon from 'sinon'
import { AIServiceCommand } from '../../../../../../src/libs/commands/ai_service/ai.service.command'
import {
  RecordPermissionChatContentAccess,
  viewSyncBreaker,
  type ConversationView,
} from '../../../../../../src/modules/enterprise_search/services/conversation/sharing/chat-content-access'

/**
 * What a viewer's page read waits for in the file permission syncs: both syncs at once, and none while the query
 * service keeps failing them, so an outage holds a read for one sync's budget at most and then not at all.
 */

const appConfig = { aiBackend: 'http://query:8000', jwtSecret: 'j', scopedJwtSecret: 's' } as never

const view = (over: Partial<ConversationView> = {}): ConversationView => ({
  appConfig,
  conversationId: 'dddddddddddddddddddddddd',
  ownerId: 'aaaaaaaaaaaaaaaaaaaaaaaa',
  viewerId: 'cccccccccccccccccccccccc',
  orgId: 'bbbbbbbbbbbbbbbbbbbbbbbb',
  page: 1,
  // An attachment on the page, so both syncs run.
  messages: [{ messageType: 'user_query', attachments: [{ recordId: 'att-1' }] }],
  ...over,
})

const timedOut = (): Error => Object.assign(new Error('The operation was aborted due to timeout'), { name: 'TimeoutError' })

describe('a viewer’s file permission syncs', () => {
  beforeEach(() => viewSyncBreaker.reset())
  afterEach(() => {
    sinon.restore()
    viewSyncBreaker.reset()
  })

  it('run side by side: the read waits for the slower one, not for both in turn', async () => {
    const started: string[] = []
    const release: Array<() => void> = []
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (this: { uri: string }) {
      started.push(this.uri)
      return new Promise((resolve) => release.push(() => resolve({ statusCode: 200, data: {}, msg: '' } as never)))
    })

    const viewed = new RecordPermissionChatContentAccess().viewed(view())
    await new Promise((resolve) => setImmediate(resolve))
    expect(started, 'both went out before either answered').to.deep.equal([
      'http://query:8000/api/v1/chat/artifacts/permissions',
      'http://query:8000/api/v1/chat/attachments/permissions',
    ])
    for (const done of release) done()
    await viewed
  })

  it('are not sent while they keep failing, and a view then waits for none', async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute').rejects(timedOut())
    const access = new RecordPermissionChatContentAccess()

    // Five timeouts in a row open the breaker: three views of two syncs each, the third view's two sent together.
    for (let i = 0; i < 3; i += 1) await access.viewed(view())
    const sent = execute.callCount
    expect(sent).to.equal(6)
    await access.viewed(view())
    expect(execute.callCount, 'nothing more is sent').to.equal(sent)
  })

  it('an answer from the service, even a refusal, keeps them going', async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 403, data: {}, msg: '' } as never)
    const access = new RecordPermissionChatContentAccess()
    for (let i = 0; i < 4; i += 1) await access.viewed(view())
    expect(execute.callCount).to.equal(8)
  })

  it("the owner's own view sends nothing", async () => {
    const execute = sinon.stub(AIServiceCommand.prototype, 'execute').resolves({ statusCode: 200, data: {}, msg: '' } as never)
    await new RecordPermissionChatContentAccess().viewed(view({ viewerId: 'aaaaaaaaaaaaaaaaaaaaaaaa' }))
    expect(execute.callCount).to.equal(0)
  })
})
