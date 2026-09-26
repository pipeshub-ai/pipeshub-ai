/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import axios from 'axios'
import { refreshSlackBotRegistry } from '../../../src/integrations/slack-bot/src/botRegistry'
import { ConfigService } from '../../../src/modules/tokens_manager/services/cm.service'

describe('slack-bot botRegistry', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('keeps each bot\'s org so its tokens resolve users only inside that org', async () => {
    sinon.stub(ConfigService, 'getInstance').returns({
      getScopedJwtSecret: sinon.stub().resolves('scoped-secret'),
    } as any)
    sinon.stub(axios, 'get').resolves({
      data: {
        configs: [
          { id: 'bot-a', botToken: 'xoxb-a', signingSecret: 's', orgId: 'org-A' },
          { id: 'bot-legacy', botToken: 'xoxb-l', signingSecret: 's', orgId: null },
        ],
      },
    })

    const bots = await refreshSlackBotRegistry({ force: true })

    expect(bots.find((b) => b.botId === 'bot-a')?.orgId).to.equal('org-A')
    expect(bots.find((b) => b.botId === 'bot-legacy')).to.not.have.property('orgId')
  })
})
