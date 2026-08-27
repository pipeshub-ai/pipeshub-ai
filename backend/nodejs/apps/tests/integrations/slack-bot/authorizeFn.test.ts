/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createAuthorizeFn, BotRegistryDeps } from '../../../src/integrations/slack-bot/src/authorizeFn'

function makeDeps(overrides: Partial<BotRegistryDeps> = {}): BotRegistryDeps {
  return {
    getCurrentMatchedSlackBot: sinon.stub().returns(null),
    getCachedSlackBots: sinon.stub().returns([]),
    refreshSlackBotRegistry: sinon.stub().resolves([]),
    findSlackBotByIdentity: sinon.stub().returns(null),
    ...overrides,
  }
}

describe('authorizeFn', () => {
  afterEach(() => {
    sinon.restore()
    delete process.env.BOT_TOKEN
    delete process.env.SLACK_BOT_ID
    delete process.env.SLACK_BOT_USER_ID
  })

  it('should return token from matched request context bot', async () => {
    const deps = makeDeps({
      getCurrentMatchedSlackBot: sinon.stub().returns({ botToken: 'ctx-token', botId: 'B1' }),
    })
    const authorize = createAuthorizeFn(deps)

    const result = await authorize({ teamId: 'T1' })
    expect(result.botToken).to.equal('ctx-token')
  })

  it('should use cached bots when available', async () => {
    const bot = { botToken: 'cached-token', botId: 'B2', botUserId: 'U2', teamId: 'T1' }
    const deps = makeDeps({
      getCachedSlackBots: sinon.stub().returns([bot]),
      findSlackBotByIdentity: sinon.stub().returns(bot),
    })
    const authorize = createAuthorizeFn(deps)

    const result = await authorize({ teamId: 'T1' })
    expect(result.botToken).to.equal('cached-token')
    expect(result.botId).to.equal('B2')
    expect(result.botUserId).to.equal('U2')
  })

  it('should refresh registry when cache is empty', async () => {
    const bot = { botToken: 'refreshed-token', botId: 'B3' }
    const refreshStub = sinon.stub().resolves([bot])
    const deps = makeDeps({
      getCachedSlackBots: sinon.stub().returns([]),
      refreshSlackBotRegistry: refreshStub,
      findSlackBotByIdentity: sinon.stub().returns(bot),
    })
    const authorize = createAuthorizeFn(deps)

    const result = await authorize({ teamId: 'T2' })
    expect(refreshStub.calledOnce).to.be.true
    expect(result.botToken).to.equal('refreshed-token')
  })

  it('should extract team_id from body payload', async () => {
    const bot = { botToken: 'body-token', botId: 'B4' }
    const findStub = sinon.stub().returns(bot)
    const deps = makeDeps({
      getCachedSlackBots: sinon.stub().returns([bot]),
      findSlackBotByIdentity: findStub,
    })
    const authorize = createAuthorizeFn(deps)

    await authorize({}, { team_id: 'T5', event: { bot_id: 'B4' } })
    const identity = findStub.firstCall.args[1]
    expect(identity.teamId).to.equal('T5')
    expect(identity.botId).to.equal('B4')
  })

  it('should extract identifiers from authorizations array', async () => {
    const bot = { botToken: 'auth-token', botId: 'B5', botUserId: 'U5' }
    const findStub = sinon.stub().returns(bot)
    const deps = makeDeps({
      getCachedSlackBots: sinon.stub().returns([bot]),
      findSlackBotByIdentity: findStub,
    })
    const authorize = createAuthorizeFn(deps)

    await authorize({}, {
      authorizations: [{ team_id: 'T6', bot_id: 'B5', user_id: 'U5' }],
    })
    const identity = findStub.firstCall.args[1]
    expect(identity.teamId).to.equal('T6')
    expect(identity.botId).to.equal('B5')
    expect(identity.botUserId).to.equal('U5')
  })

  it('should extract team id from nested team.id', async () => {
    const bot = { botToken: 'nested-token' }
    const findStub = sinon.stub().returns(bot)
    const deps = makeDeps({
      getCachedSlackBots: sinon.stub().returns([bot]),
      findSlackBotByIdentity: findStub,
    })
    const authorize = createAuthorizeFn(deps)

    await authorize({}, { team: { id: 'T7' } })
    const identity = findStub.firstCall.args[1]
    expect(identity.teamId).to.equal('T7')
  })

  it('should extract team from event payload', async () => {
    const bot = { botToken: 'event-token' }
    const findStub = sinon.stub().returns(bot)
    const deps = makeDeps({
      getCachedSlackBots: sinon.stub().returns([bot]),
      findSlackBotByIdentity: findStub,
    })
    const authorize = createAuthorizeFn(deps)

    await authorize({}, { event: { team: 'T8' } })
    const identity = findStub.firstCall.args[1]
    expect(identity.teamId).to.equal('T8')
  })

  it('should fall back to BOT_TOKEN env var when no match found', async () => {
    process.env.BOT_TOKEN = 'env-fallback-token'
    process.env.SLACK_BOT_ID = 'env-bot-id'
    process.env.SLACK_BOT_USER_ID = 'env-bot-user-id'

    const deps = makeDeps()
    const authorize = createAuthorizeFn(deps)

    const result = await authorize({ teamId: 'T9' })
    expect(result.botToken).to.equal('env-fallback-token')
    expect(result.botId).to.equal('env-bot-id')
    expect(result.botUserId).to.equal('env-bot-user-id')
  })

  it('should throw when no token can be resolved', async () => {
    const deps = makeDeps()
    const authorize = createAuthorizeFn(deps)

    try {
      await authorize({ teamId: 'T10' })
      expect.fail('should have thrown')
    } catch (err: any) {
      expect(err.message).to.include('Unable to resolve Slack bot token')
    }
  })

  it('should handle null/undefined body', async () => {
    process.env.BOT_TOKEN = 'fallback'
    const deps = makeDeps()
    const authorize = createAuthorizeFn(deps)

    const result = await authorize({}, null as any)
    expect(result.botToken).to.equal('fallback')
  })

  it('should handle non-object body', async () => {
    process.env.BOT_TOKEN = 'fallback'
    const deps = makeDeps()
    const authorize = createAuthorizeFn(deps)

    const result = await authorize({}, 'not-object')
    expect(result.botToken).to.equal('fallback')
  })

  it('should ignore empty or whitespace-only string fields', async () => {
    const bot = { botToken: 'tok' }
    const findStub = sinon.stub().returns(bot)
    const deps = makeDeps({
      getCachedSlackBots: sinon.stub().returns([bot]),
      findSlackBotByIdentity: findStub,
    })
    const authorize = createAuthorizeFn(deps)

    await authorize({}, { team_id: '  ', event: { bot_id: '' } })
    const identity = findStub.firstCall.args[1]
    expect(identity.teamId).to.be.undefined
    expect(identity.botId).to.be.undefined
  })

  it('should ignore non-string field values', async () => {
    const bot = { botToken: 'tok' }
    const findStub = sinon.stub().returns(bot)
    const deps = makeDeps({
      getCachedSlackBots: sinon.stub().returns([bot]),
      findSlackBotByIdentity: findStub,
    })
    const authorize = createAuthorizeFn(deps)

    await authorize({}, { team_id: 123, event: { bot_id: null } })
    const identity = findStub.firstCall.args[1]
    expect(identity.teamId).to.be.undefined
    expect(identity.botId).to.be.undefined
  })

  it('should prefer params.teamId over body teamId', async () => {
    const bot = { botToken: 'tok' }
    const findStub = sinon.stub().returns(bot)
    const deps = makeDeps({
      getCachedSlackBots: sinon.stub().returns([bot]),
      findSlackBotByIdentity: findStub,
    })
    const authorize = createAuthorizeFn(deps)

    await authorize({ teamId: 'PARAMS_TEAM' }, { team_id: 'BODY_TEAM' })
    const identity = findStub.firstCall.args[1]
    expect(identity.teamId).to.equal('PARAMS_TEAM')
  })

  it('should handle authorizations array with no valid entries', async () => {
    process.env.BOT_TOKEN = 'fallback'
    const deps = makeDeps()
    const authorize = createAuthorizeFn(deps)

    const result = await authorize({}, { authorizations: [null, undefined] })
    expect(result.botToken).to.equal('fallback')
  })
})
