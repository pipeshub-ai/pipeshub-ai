import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { CollaborationEmailIntents } from '../../../../../../src/modules/enterprise_search/services/collaboration/notify/collaboration-email-intents'
import { EmailTemplateType } from '../../../../../../src/modules/mail/middlewares/types'

function build(over: { smtp?: boolean | Error; shareEmails?: boolean; chatShared?: boolean; actorName?: string } = {}) {
  const preferences = { get: sinon.stub().callsFake(async (_o: string, userId: string) => ({ email: { chatShared: userId !== 'muted' && over.chatShared !== false, ownershipTransferred: true }, inApp: { chatActivity: true }, mutedSessions: [] })) }
  const users = { displayNames: sinon.stub().resolves(new Map([['actor', over.actorName ?? 'Alice']])), findByIds: sinon.stub() }
  const orgs = { displayName: sinon.stub().resolves('Acme') }
  const smtp = sinon.stub().callsFake(async () => {
    if (over.smtp instanceof Error) throw over.smtp
    return over.smtp ?? true
  })
  const intents = new CollaborationEmailIntents({
    isSmtpConfigured: smtp,
    flags: { isEnabled: sinon.stub().resolves(over.shareEmails ?? true) },
    preferences: preferences as never,
    users,
    orgs,
  })
  return { intents, preferences, users, orgs }
}

const ask = (intents: CollaborationEmailIntents, ids: string[]) =>
  intents.forDirectUsers({ orgId: 'o', actorUserId: 'actor', template: EmailTemplateType.ChatShared, recipients: ids.map((userId) => ({ userId, accessLevel: 'read' as const })) })

describe('CollaborationEmailIntents', () => {
  it('NT-10..12: no intent, and no further lookups, while SMTP or the platform flag is off or the SMTP check fails', async () => {
    for (const over of [{ smtp: false }, { shareEmails: false }, { smtp: new Error('kv down') }]) {
      const { intents, preferences, orgs } = build(over)
      expect((await ask(intents, ['b'])).size).to.equal(0)
      expect(preferences.get.called).to.equal(false)
      expect(orgs.displayName.called).to.equal(false)
    }
  })

  it('sets an intent per recipient who has not turned the email off', async () => {
    const { intents } = build()
    const out = await ask(intents, ['b', 'muted'])
    expect([...out.keys()]).to.deep.equal(['b'])
    expect(out.get('b')).to.deep.equal({ template: 'chatShared', actorName: 'Alice', orgName: 'Acme', accessLevel: 'read' })
  })

  it('names an unknown actor "Someone" and asks nothing for no recipients', async () => {
    const { intents, users } = build({ actorName: '' })
    expect((await ask(intents, ['b'])).get('b')?.actorName).to.equal('Someone')
    expect(users.displayNames.firstCall.args[2]).to.deep.equal({ emailFallback: false })
    users.displayNames.resetHistory()
    expect((await ask(intents, [])).size).to.equal(0)
    expect(users.displayNames.called).to.equal(false)
  })
})
