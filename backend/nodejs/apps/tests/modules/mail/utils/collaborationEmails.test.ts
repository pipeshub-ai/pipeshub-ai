import 'reflect-metadata'
import { expect } from 'chai'
import fs from 'fs'
import path from 'path'
import { getEmailContent } from '../../../../src/modules/mail/utils/email-content'
import { isChatCollaborationEmailData } from '../../../../src/modules/mail/utils/emailTemplates'
import { EmailTemplateType } from '../../../../src/modules/mail/middlewares/types'

const layoutsDir = path.resolve(
  __dirname,
  '../../../../src/modules/mail/views/layouts/collaboration',
)
const TEMPLATES = [
  EmailTemplateType.ChatShared,
  EmailTemplateType.ChatOwnershipTransferred,
] as const

const data = () => ({
  actorName: 'Alice Owner',
  orgName: 'Acme',
  accessLevel: 'write' as const,
  openUrl: 'https://app.example.com/chat?conversationId=abc123',
  settingsUrl: 'https://app.example.com/workspace/profile',
})

describe('mail collaboration templates', () => {
  it('use escaped {{ }} only', () => {
    for (const file of ['chatShared.hbs', 'chatOwnershipTransferred.hbs']) {
      expect(fs.readFileSync(path.join(layoutsDir, file), 'utf8'), file).to.not.include('{{{')
    }
  })

  for (const type of TEMPLATES) {
    describe(type, () => {
      it('renders actor, org, access level and links, and nothing chat-specific', () => {
        const html = getEmailContent(type, { ...data() })
        expect(html).to.include('Alice Owner')
        expect(html).to.include('Acme')
        expect(html).to.include('<strong>write</strong>')
        expect(html).to.include('https://app.example.com/chat?conversationId&#x3D;abc123')
        expect(html).to.include('https://app.example.com/workspace/profile')
        expect(html).to.not.match(/\{\{/)
      })

      it('escapes HTML in actorName', () => {
        const html = getEmailContent(type, {
          ...data(),
          actorName: '<script>alert(1)</script>',
        })
        expect(html).to.not.include('<script>alert(1)</script>')
        expect(html).to.include('&lt;script&gt;alert(1)&lt;/script&gt;')
      })

      for (const extra of ['title', 'chatTitle', 'note', 'content', 'email']) {
        it(`rejects an extra "${extra}" field`, () => {
          expect(() =>
            getEmailContent(type, { ...data(), [extra]: 'Q3 layoffs plan' }),
          ).to.throw(/requires exactly/)
        })
      }

      it('rejects a missing field and a bad access level', () => {
        const { openUrl: _omit, ...missing } = data()
        expect(() => getEmailContent(type, missing)).to.throw(/requires exactly/)
        expect(() =>
          getEmailContent(type, { ...data(), accessLevel: 'owner' }),
        ).to.throw(/requires exactly/)
      })
    })
  }

  describe('chatMentioned', () => {
    const mention = () => ({ actorName: 'Bob <b>', orgName: 'Acme', openUrl: 'https://app.example.com/chat?conversationId=abc', settingsUrl: 'https://app.example.com/workspace/profile' })

    it('renders actor, org and links, escaped, without an access level', () => {
      const html = getEmailContent(EmailTemplateType.ChatMentioned, mention())
      expect(html).to.include('Bob &lt;b&gt;')
      expect(html).to.include('mentioned you')
      expect(html).to.include('Acme')
      expect(html).to.not.match(/\{\{/)
      expect(fs.readFileSync(path.join(layoutsDir, 'chatMentioned.hbs'), 'utf8')).to.not.include('{{{')
    })

    for (const extra of ['title', 'chatTitle', 'note', 'content', 'message', 'accessLevel']) {
      it(`rejects an extra "${extra}" field`, () => {
        expect(() => getEmailContent(EmailTemplateType.ChatMentioned, { ...mention(), [extra]: 'Q3 layoffs plan' })).to.throw(/requires exactly/)
      })
    }
  })

  it('guard rejects non-objects', () => {
    expect(isChatCollaborationEmailData(null)).to.equal(false)
    expect(isChatCollaborationEmailData('x')).to.equal(false)
    expect(isChatCollaborationEmailData(data())).to.equal(true)
  })
})
