/// <reference types="mocha" />
/**
 * BOTSEAM-1: the Slack bot's extension point. An edition installs the org a bot serves and its thread map before
 * the bot loads, and the bot uses them for the links, the token and the thread map; nothing of the bot is copied.
 */
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import jwt from 'jsonwebtoken'
import axios from 'axios'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import * as soleOrg from '../../../src/modules/slack_link/sole-org'
import {
  asBotOrg,
  defaultSlackBotEdition,
  installSlackBotEdition,
  resetSlackBotEditionForTests,
  slackBotEdition,
} from '../../../src/integrations/slack-bot/src/edition'
import { getFromDatabase, saveToDatabase } from '../../../src/integrations/slack-bot/src/utils/conversation'
import {
  refreshSlackBotRegistry,
  resetSlackBotRegistryCache,
  type SlackBotConfig,
} from '../../../src/integrations/slack-bot/src/botRegistry'
import { setSlackAuthTestForTests } from '../../../src/integrations/slack-bot/src/identity/home'
import { ConfigService } from '../../../src/modules/tokens_manager/services/cm.service'
import { getCurrentOrgId } from '../../../src/libs/context/orgContext'
import type { SlackThreadRepository } from '../../../src/libs/repositories/slack-thread'
import { slackJwtGenerator } from '../../../src/libs/utils/createJwt'
import * as scopedRequest from '../../../src/modules/enterprise_search/utils/scoped-request'

const BOT: SlackBotConfig = { botToken: 'xoxb-1', signingSecret: 's', botId: 'cfg-1', agentId: null }

/** A thread map that records the org context each call ran in. */
class OrgRecordingThreads implements SlackThreadRepository {
  readonly seen: Array<{ op: string; orgId: string | undefined }> = []
  async getConversationId(): Promise<string | null> {
    this.seen.push({ op: 'get', orgId: getCurrentOrgId() })
    return 'conv-1'
  }
  async saveConversationId(): Promise<void> {
    this.seen.push({ op: 'save', orgId: getCurrentOrgId() })
  }
}

describe('Slack bot edition seam (BOTSEAM-1)', () => {
  afterEach(() => {
    sinon.restore()
    resetSlackBotEditionForTests()
  })

  it('serves the install\'s only organisation by default', async () => {
    sinon.stub(soleOrg, 'resolveSoleOrgId').resolves('org-sole')
    expect(slackBotEdition()).to.equal(defaultSlackBotEdition)
    expect(await slackBotEdition().orgOfBot({ ...BOT, orgId: 'org-claimed' })).to.equal('org-sole')
  })

  it('an installed edition decides the org; a bot it serves no org for answers nobody', async () => {
    installSlackBotEdition({
      orgOfBot: async (bot) => {
        if (!bot.orgId) throw new Error('Slack bot configuration names no organization.')
        return bot.orgId
      },
    })
    expect(await slackBotEdition().orgOfBot({ ...BOT, orgId: 'org-a' })).to.equal('org-a')
    const refused = await slackBotEdition().orgOfBot(BOT).then(() => 'answered', (e: Error) => e.message)
    expect(refused).to.equal('Slack bot configuration names no organization.')
  })

  it('an installed thread map is the bot\'s, and every read and write runs as the bot\'s org', async () => {
    const threads = new OrgRecordingThreads()
    installSlackBotEdition({ threadRepository: threads })
    expect(await asBotOrg('org-a', () => getFromDatabase('t1', 'cfg-1', 'a@x.test'))).to.equal('conv-1')
    await asBotOrg('org-a', () => saveToDatabase({ threadId: 't1', conversationId: 'conv-1', botId: 'cfg-1', email: 'a@x.test' }))
    expect(threads.seen).to.deep.equal([
      { op: 'get', orgId: 'org-a' },
      { op: 'save', orgId: 'org-a' },
    ])
  })

  it('the reset gives back the default org and thread map', async () => {
    const threads = new OrgRecordingThreads()
    installSlackBotEdition({ orgOfBot: async () => 'org-x', threadRepository: threads })
    resetSlackBotEditionForTests()
    expect(slackBotEdition()).to.equal(defaultSlackBotEdition)
  })

  it('the bot takes the org from the edition, names it on its token and keeps its thread map as that org', () => {
    const index = readFileSync(join(__dirname, '../../../src/integrations/slack-bot/src/index.ts'), 'utf8')
    expect(index).to.contain('orgId: await slackBotEdition().orgOfBot(bot)')
    expect(index).to.not.match(/resolveSoleOrgId/)
    expect(index).to.match(/\{ configId: currentBotId, orgId: caller\.orgId,/)
    expect(index).to.match(/asBotOrg\(caller\.orgId, \(\) => getFromDatabase\(/)
    expect(index).to.match(/asBotOrg\(caller\.orgId, \(\) => saveToDatabase\(/)
    expect(index.match(/\b(getFromDatabase|saveToDatabase)\(/g)).to.have.length(2)
  })

  it('puts the org the bot serves on its token, and none when it names none', () => {
    const named = jwt.verify(slackJwtGenerator('', 'k', undefined, { configId: 'cfg-1', orgId: 'org-a' }), 'k') as Record<string, unknown>
    expect(named).to.include({ configId: 'cfg-1', orgId: 'org-a' })
    const unnamed = jwt.verify(slackJwtGenerator('', 'k', undefined, { configId: 'cfg-1' }), 'k') as Record<string, unknown>
    expect(unnamed).to.not.have.property('orgId')
  })

  describe('the gateway checks the org the token names', () => {
    const appConfig = { jwtSecret: 'session-secret', scopedJwtSecret: 'scoped-secret' } as never
    const request = (orgId?: string) =>
      ({
        headers: { authorization: 'Bearer slack' },
        params: {},
        tokenPayload: { configId: 'cfg-1', slackTeamId: 'T1', slackUserId: 'U1', ...(orgId ? { orgId } : {}) },
      }) as never as { user?: unknown }

    it('refuses a token whose org is not the one this gateway resolves for the bot', async () => {
      sinon.stub(scopedRequest, 'resolveSoleOrgId').resolves('org-sole')
      const req = request('org-other')
      const error = await scopedRequest.hydrateScopedRequestAsUser(req as never, appConfig).then(() => null, (e: any) => e)
      expect(error?.statusCode).to.equal(401)
      expect(String(error?.message)).to.contain('does not match the organisation this bot serves')
      expect(req.user).to.equal(undefined)
    })

    for (const orgId of ['org-sole', undefined]) {
      it(`goes on to the person's link when the token names ${orgId ?? 'no org'}`, async () => {
        sinon.stub(scopedRequest, 'resolveSoleOrgId').resolves('org-sole')
        const bind = sinon.stub(scopedRequest, 'bindScopedRequestToSlackAccount').resolves()
        await scopedRequest.hydrateScopedRequestAsUser(request(orgId) as never, appConfig)
        expect(bind.calledOnce).to.equal(true)
        expect(bind.firstCall.args[2]).to.equal('org-sole')
      })
    }
  })

  describe('the registry keeps the org the gateway files a configuration under', () => {
    beforeEach(() => {
      resetSlackBotRegistryCache()
      sinon.stub(ConfigService, 'getInstance').returns({ getScopedJwtSecret: async () => 'k' } as unknown as ConfigService)
      setSlackAuthTestForTests(async () => ({ ok: true, team_id: 'T1', user_id: 'UBOT' }))
    })
    afterEach(() => {
      setSlackAuthTestForTests()
      resetSlackBotRegistryCache()
    })

    it('reads orgId when the gateway sends one, and adds none when it does not', async () => {
      sinon.stub(axios, 'get').resolves({
        data: {
          configs: [
            { botToken: 'xoxb-a', signingSecret: 's', id: 'cfg-a', orgId: 'org-a' },
            { botToken: 'xoxb-b', signingSecret: 's', id: 'cfg-b' },
          ],
        },
      })
      const bots = await refreshSlackBotRegistry({ force: true })
      expect(bots.map((bot) => [bot.botId, bot.orgId])).to.deep.equal([
        ['cfg-a', 'org-a'],
        ['cfg-b', undefined],
      ])
      expect(bots[1]).to.not.have.property('orgId')
    })
  })
})
