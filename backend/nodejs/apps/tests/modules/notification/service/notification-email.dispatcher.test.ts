import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  EmailDispatchInput,
  NotificationEmailDispatcher,
} from '../../../../src/modules/notification/service/notification-email.dispatcher'
import { EmailTemplateType } from '../../../../src/modules/mail/middlewares/types'
import { getEmailContent } from '../../../../src/modules/mail/utils/email-content'

const ORG = '64b000000000000000000001'
const USER = '64b000000000000000000002'
const EMAIL = 'bob.secret@example.com'

const input = (over: Partial<EmailDispatchInput> = {}): EmailDispatchInput => ({
  orgId: ORG,
  assignedTo: USER,
  dedupeKey: 'chat.shared:sess1:rev7',
  redirectLink: '/chat?conversationId=sess1',
  emailIntent: {
    template: EmailTemplateType.ChatShared,
    actorName: 'Alice',
    orgName: 'Acme',
    accessLevel: 'read',
  },
  ...over,
})

describe('NotificationEmailDispatcher', () => {
  let publishEvent: sinon.SinonStub
  let findEmail: sinon.SinonStub
  let logger: Record<'info' | 'warn' | 'error' | 'debug', sinon.SinonStub>
  let dispatcher: NotificationEmailDispatcher

  beforeEach(() => {
    publishEvent = sinon.stub().resolves()
    findEmail = sinon.stub().resolves(EMAIL)
    logger = {
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
      debug: sinon.stub(),
    }
    dispatcher = new NotificationEmailDispatcher(
      { publishEvent },
      { findEmail },
      'https://app.example.com/',
      logger as any,
    )
  })

  const logged = () =>
    JSON.stringify(Object.values(logger).flatMap((s) => s.args))

  it('PH06-09: publishes one mail event addressed to the looked-up email', async () => {
    await dispatcher.dispatch(input())

    expect(findEmail.calledOnceWith(ORG, USER)).to.equal(true)
    expect(publishEvent.calledOnce).to.equal(true)
    const event = publishEvent.firstCall.args[0]
    expect(event.eventType).to.equal('sendMail')
    expect(event.payload.orgId).to.equal(ORG)
    expect(event.payload.mail.sendEmailTo).to.deep.equal([EMAIL])
    expect(event.payload.mail.emailTemplateType).to.equal('chatShared')
    expect(event.payload.mail.templateData).to.deep.equal({
      actorName: 'Alice',
      orgName: 'Acme',
      accessLevel: 'read',
      openUrl: 'https://app.example.com/chat?conversationId=sess1',
      settingsUrl: 'https://app.example.com/workspace/profile',
    })
  })

  it('builds template data the mail renderer accepts, for both templates', async () => {
    for (const template of [
      EmailTemplateType.ChatShared,
      EmailTemplateType.ChatOwnershipTransferred,
    ] as const) {
      publishEvent.resetHistory()
      await dispatcher.dispatch(
        input({
          dedupeKey: `k-${template}`,
          emailIntent: { ...input().emailIntent, template },
        }),
      )
      const mail = publishEvent.firstCall.args[0].payload.mail
      expect(() => getEmailContent(mail.emailTemplateType, mail.templateData)).to.not.throw()
    }
  })

  it('PR-10.5: a mention email carries no access level and no chat content, and is capped to one a day per chat', async () => {
    const hasEarlier = sinon.stub().resolves(false)
    const throttled = new NotificationEmailDispatcher({ publishEvent }, { findEmail }, 'https://app.example.com/', logger as any, { hasEarlier })
    const emailIntent = { template: EmailTemplateType.ChatMentioned, actorName: 'Bob', orgName: 'Acme' } as const
    const notification = { id: 'n1', type: 'chat.mentioned', sessionId: 'sess1' }
    await throttled.dispatch(input({ dedupeKey: 'chat.mentioned:m1:u1', emailIntent, notification }))
    const mail = publishEvent.firstCall.args[0].payload.mail
    expect(mail.subject).to.equal('Bob mentioned you in a conversation')
    expect(mail.templateData).to.deep.equal({
      actorName: 'Bob',
      orgName: 'Acme',
      openUrl: 'https://app.example.com/chat?conversationId=sess1',
      settingsUrl: 'https://app.example.com/workspace/profile',
    })
    expect(() => getEmailContent(mail.emailTemplateType, mail.templateData)).to.not.throw()
    expect(hasEarlier.firstCall.args[0]).to.include({ type: 'chat.mentioned', sessionId: 'sess1' })
    hasEarlier.resolves(true)
    await throttled.dispatch(input({ dedupeKey: 'chat.mentioned:m2:u1', emailIntent, notification: { ...notification, id: 'n2' } }))
    expect(publishEvent.callCount).to.equal(1)
  })

  it('publishes at most once per dedupeKey', async () => {
    await dispatcher.dispatch(input())
    await dispatcher.dispatch(input())
    expect(publishEvent.callCount).to.equal(1)
    await dispatcher.dispatch(input({ dedupeKey: 'other' }))
    expect(publishEvent.callCount).to.equal(2)
  })

  it('skips without a dedupeKey', async () => {
    await dispatcher.dispatch(input({ dedupeKey: undefined }))
    expect(publishEvent.called).to.equal(false)
    expect(logger.warn.calledOnce).to.equal(true)
  })

  it('skips and logs when the user is missing or has no email', async () => {
    findEmail.resolves(null)
    await dispatcher.dispatch(input())
    expect(publishEvent.called).to.equal(false)
    expect(logger.info.calledOnce).to.equal(true)
  })

  it('skips a link that is not an app-relative path', async () => {
    await dispatcher.dispatch(input({ redirectLink: 'https://evil.example.com/x' }))
    await dispatcher.dispatch(input({ redirectLink: '//evil.example.com' }))
    await dispatcher.dispatch(input({ redirectLink: undefined }))
    expect(publishEvent.called).to.equal(false)
  })

  it('logs and does not throw when the producer fails, and does not retry', async () => {
    publishEvent.rejects(new Error('broker down'))
    await dispatcher.dispatch(input())
    expect(logger.error.calledOnce).to.equal(true)
    await dispatcher.dispatch(input())
    expect(publishEvent.callCount).to.equal(1)
  })

  it('logs and does not throw when the lookup fails', async () => {
    findEmail.rejects(new Error('mongo down'))
    await dispatcher.dispatch(input())
    expect(publishEvent.called).to.equal(false)
    expect(logger.error.calledOnce).to.equal(true)
  })

  it('F-12: one email per recipient, type and chat a day; a share/unshare loop does not mail every round', async () => {
    const hasEarlier = sinon.stub().resolves(false)
    const throttled = new NotificationEmailDispatcher({ publishEvent }, { findEmail }, 'https://app.example.com/', logger as any, { hasEarlier })
    const notification = { id: 'n1', type: 'chat.shared', sessionId: 'sess1' }
    await throttled.dispatch(input({ notification }))
    expect(publishEvent.callCount).to.equal(1)
    const q = hasEarlier.firstCall.args[0]
    expect(q).to.include({ orgId: ORG, userId: USER, type: 'chat.shared', sessionId: 'sess1', notificationId: 'n1' })
    expect(Date.now() - q.since.getTime()).to.be.closeTo(24 * 60 * 60 * 1000, 5000)

    hasEarlier.resolves(true)
    await throttled.dispatch(input({ dedupeKey: 'chat.shared:sess1:9', notification: { ...notification, id: 'n2' } }))
    expect(publishEvent.callCount).to.equal(1)
    expect(findEmail.callCount).to.equal(1)
  })

  it('without a chat id the daily check is not asked', async () => {
    const hasEarlier = sinon.stub().resolves(true)
    const throttled = new NotificationEmailDispatcher({ publishEvent }, { findEmail }, 'https://app.example.com/', logger as any, { hasEarlier })
    await throttled.dispatch(input({ notification: { id: 'n1', type: 'chat.shared' } }))
    expect(hasEarlier.called).to.equal(false)
    expect(publishEvent.callCount).to.equal(1)
  })

  it('never logs the recipient address', async () => {
    await dispatcher.dispatch(input())
    findEmail.resolves(null)
    await dispatcher.dispatch(input({ dedupeKey: 'b' }))
    publishEvent.rejects(new Error('broker down'))
    findEmail.resolves(EMAIL)
    await dispatcher.dispatch(input({ dedupeKey: 'c' }))
    expect(logged()).to.not.include(EMAIL)
    expect(logged()).to.not.include('secret')
  })
})
