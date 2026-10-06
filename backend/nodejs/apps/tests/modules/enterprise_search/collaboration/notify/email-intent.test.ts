import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  decideEmailIntent,
  resolveEmailIntentGates,
  EmailIntentInput,
} from '../../../../../src/modules/enterprise_search/services/collaboration/notify/email-intent'
import { EmailTemplateType } from '../../../../../src/modules/mail/middlewares/types'
import { COLLAB_FLAG_KEYS, PLATFORM_FEATURE_FLAGS } from '../../../../../src/modules/configuration_manager/constants/constants'

const base = (over: Partial<EmailIntentInput> = {}): EmailIntentInput => ({
  template: EmailTemplateType.ChatShared,
  isDirectRecipient: true,
  gates: { smtpConfigured: true, shareEmailsEnabled: true },
  preferences: { email: { chatShared: true, ownershipTransferred: true } },
  actorName: 'Alice',
  orgName: 'Acme',
  accessLevel: 'write',
  ...over,
})

describe('collaboration email intent', () => {
  it('sets the intent when every condition holds', () => {
    expect(decideEmailIntent(base())).to.deep.equal({
      template: 'chatShared',
      actorName: 'Alice',
      orgName: 'Acme',
      accessLevel: 'write',
    })
  })

  it('NT-10: no intent when SMTP is not configured', () => {
    expect(decideEmailIntent(base({ gates: { smtpConfigured: false, shareEmailsEnabled: true } }))).to.equal(undefined)
  })

  it('NT-11: no intent when the recipient turned the email off', () => {
    expect(decideEmailIntent(base({ preferences: { email: { chatShared: false, ownershipTransferred: true } } }))).to.equal(undefined)
    expect(
      decideEmailIntent(
        base({
          template: EmailTemplateType.ChatOwnershipTransferred,
          preferences: { email: { chatShared: true, ownershipTransferred: false } },
        }),
      ),
    ).to.equal(undefined)
  })

  it('a preference for the other template does not block this one', () => {
    const intent = decideEmailIntent(
      base({
        template: EmailTemplateType.ChatOwnershipTransferred,
        preferences: { email: { chatShared: false, ownershipTransferred: true } },
      }),
    )
    expect(intent?.template).to.equal('chatOwnershipTransferred')
  })

  describe('chat.mentioned (PR-10.5)', () => {
    const mention = (over: Partial<EmailIntentInput> = {}) => base({ template: EmailTemplateType.ChatMentioned, accessLevel: undefined, ...over })
    const prefs = (chatMentioned?: boolean) => ({ email: { chatShared: true, ownershipTransferred: true, ...(chatMentioned !== undefined && { chatMentioned }) } })

    it('is off unless the recipient opted in: no row, an old row without the field, and false all mean no email', () => {
      expect(decideEmailIntent(mention({ preferences: null }))).to.equal(undefined)
      expect(decideEmailIntent(mention({ preferences: prefs() }))).to.equal(undefined)
      expect(decideEmailIntent(mention({ preferences: prefs(false) }))).to.equal(undefined)
    })

    it('opted in: the intent carries the actor and org only', () => {
      expect(decideEmailIntent(mention({ preferences: prefs(true) }))).to.deep.equal({ template: 'chatMentioned', actorName: 'Alice', orgName: 'Acme' })
    })

    it('still needs SMTP, the share-email flag and a direct recipient', () => {
      expect(decideEmailIntent(mention({ preferences: prefs(true), gates: { smtpConfigured: false, shareEmailsEnabled: true } }))).to.equal(undefined)
      expect(decideEmailIntent(mention({ preferences: prefs(true), gates: { smtpConfigured: true, shareEmailsEnabled: false } }))).to.equal(undefined)
      expect(decideEmailIntent(mention({ preferences: prefs(true), isDirectRecipient: false }))).to.equal(undefined)
    })

    it('the share and transfer preferences do not leak into it', () => {
      expect(decideEmailIntent(base({ preferences: prefs() }))).to.not.equal(undefined)
      expect(decideEmailIntent(mention({ preferences: { email: { chatShared: true, ownershipTransferred: true } } }))).to.equal(undefined)
    })
  })

  it('defaults to on when the recipient has no preferences row', () => {
    expect(decideEmailIntent(base({ preferences: null }))).to.not.equal(undefined)
  })

  it('NT-12: no intent when ENABLE_CHAT_SHARE_EMAILS is off', () => {
    expect(decideEmailIntent(base({ gates: { smtpConfigured: true, shareEmailsEnabled: false } }))).to.equal(undefined)
  })

  it('no intent for team or org-wide recipients', () => {
    expect(decideEmailIntent(base({ isDirectRecipient: false }))).to.equal(undefined)
  })

  describe('resolveEmailIntentGates', () => {
    it('reads the smtp status and the platform flag', async () => {
      const isEnabled = sinon.stub().resolves(false)
      const gates = await resolveEmailIntentGates({
        isSmtpConfigured: async () => true,
        flags: { isEnabled },
      })
      expect(gates).to.deep.equal({ smtpConfigured: true, shareEmailsEnabled: false })
      expect(isEnabled.calledWith('ENABLE_CHAT_SHARE_EMAILS')).to.equal(true)
    })

    it('closes the SMTP gate instead of throwing when the lookup fails', async () => {
      const gates = await resolveEmailIntentGates({
        isSmtpConfigured: async () => {
          throw new Error('kv down')
        },
        flags: { isEnabled: async () => true },
      })
      expect(gates).to.deep.equal({ smtpConfigured: false, shareEmailsEnabled: true })
    })
  })

  it('declares ENABLE_CHAT_SHARE_EMAILS as a Labs flag defaulting to on', () => {
    const def = PLATFORM_FEATURE_FLAGS.find((d) => d.key === COLLAB_FLAG_KEYS.chatShareEmails)
    expect(def).to.include({ key: 'ENABLE_CHAT_SHARE_EMAILS', defaultEnabled: true })
    expect(def!.hidden ?? false).to.equal(false)
  })
})
