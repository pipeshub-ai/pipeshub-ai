import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { ConversationEventProducers } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/conversation-event-producers'
import { OutboxCollaborationNotifier } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/collaboration-notifier'
import { RecipientResolver } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/recipient-resolver'
import { CollaborationEmailIntents } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/collaboration-email-intents'
import { notifiableMentions } from '../../../../../src/modules/enterprise_search/services/collaboration/notify/mention-principals'
import { MutationEffects } from '../../../../../src/modules/enterprise_search/services/collaboration/mutation/mutation-effects'
import { NotificationBrokerMessage } from '../../../../../src/modules/notification/utils/notification-payload.resolver'
import { MentionRef } from '../../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.types'
import { orgWideTeamId } from '../../../../../src/modules/enterprise_search/services/collaboration/principals/principal-resolver'

const ORG = '6abf35278cf29be1a243f0aa'
const SESSION = '6abf35278cf29be1a243f0bb'
const MESSAGE = '6abf35278cf29be1a243f0cc'
const OWNER = 'owner'
const identity = { userId: 'bob', orgId: ORG, authHeaders: {}, requestKey: {} }
const user = (id: string): MentionRef => ({ type: 'user', id })
const team = (id: string): MentionRef => ({ type: 'team', id })

interface Setup {
  /** Direct collaborators besides the owner. */
  direct?: string[]
  teams?: string[]
  members?: Record<string, string[]>
  /** Users that exist in the org; everyone named anywhere by default. */
  known?: string[]
  emailPref?: Record<string, boolean>
  smtp?: boolean
  shareEmails?: boolean
}

function build(setup: Setup = {}) {
  const direct = setup.direct ?? ['bob', 'carol']
  const teams = setup.teams ?? []
  const sharedWith = [
    ...direct.map((id) => ({ principalType: 'user', userId: id, accessLevel: 'write' })),
    ...teams.map((teamId) => ({ principalType: 'team', teamId, accessLevel: 'read' })),
  ]
  const session = { _id: new Types.ObjectId(SESSION), orgId: new Types.ObjectId(ORG), userId: OWNER, sharedWith } as never
  const memberUserIds = sinon.stub().callsFake(async (teamId: string, _i: unknown, opts: { limit: number }) => ({
    status: 'ok' as const,
    userIds: (setup.members?.[teamId] ?? []).slice(0, opts.limit),
  }))
  const everyone = new Set([OWNER, ...direct, ...Object.values(setup.members ?? {}).flat(), ...(setup.known ?? ['dan', 'erin'])])
  const users = {
    findByIds: async (_o: string, ids: readonly string[]) => ids.filter((id) => everyone.has(id)).map((userId) => ({ userId, displayName: userId, kind: 'human' as const, isDisabled: false })),
    displayNames: async (_o: string, ids: readonly string[]) => new Map(ids.map((id) => [id, id === 'bob' ? 'Bob' : id])),
  }
  const recipients = new RecipientResolver(users as never, { memberUserIds } as never, { warn: sinon.stub() } as never)
  const written: NotificationBrokerMessage[][] = []
  const outbox = { write: sinon.stub().callsFake(async (rows: NotificationBrokerMessage[]) => void written.push([...rows])) }
  const notifier = new OutboxCollaborationNotifier({ recipients, outbox, archiver: { archiveShared: sinon.stub() } as never })
  const emailIntents = new CollaborationEmailIntents({
    isSmtpConfigured: async () => setup.smtp ?? false,
    flags: { isEnabled: async () => setup.shareEmails ?? true } as never,
    preferences: {
      get: async (_o: string, id: string) => ({
        email: { chatShared: true, ownershipTransferred: true, ...(setup.emailPref && id in setup.emailPref && { chatMentioned: setup.emailPref[id] }) },
        inApp: { chatActivity: true },
        mutedSessions: [],
      }),
    } as never,
    users: users as never,
    orgs: { displayName: async () => 'Acme' } as never,
  })
  const producers = new ConversationEventProducers({
    audience: {} as never,
    readState: {} as never,
    preferences: {} as never,
    notifier,
    effects: new MutationEffects({ record: async () => undefined }, notifier),
    recipients,
    emailIntents,
    logger: { warn: sinon.stub(), error: sinon.stub() },
  })
  const mentioned = (mentions: MentionRef[], actorUserId = 'bob') => producers.mentioned({ session, identity, actorUserId, messageId: MESSAGE, mentions })
  return { mentioned, written, outbox, memberUserIds, rows: () => written.flat() }
}

describe('chat.mentioned notifications', () => {
  it('a direct mention of a participant is one in-app row with a per-recipient dedupe key and no content', async () => {
    const { mentioned, rows } = build()
    await mentioned([user('carol')])
    expect(rows()).to.have.length(1)
    expect(rows()[0]).to.deep.include({
      orgId: ORG,
      type: 'chat.mentioned',
      recipientUserIds: ['carol'],
      dedupeKey: `chat.mentioned:${MESSAGE}:carol`,
      redirectLink: `/chat?conversationId=${SESSION}`,
      severity: 'info',
    })
    expect(rows()[0]!.payload).to.deep.equal({ sessionId: SESSION, kind: 'chat', actorUserId: 'bob', messageId: MESSAGE })
    expect(rows()[0]).to.not.have.property('emailIntent')
    expect(JSON.stringify(rows()[0])).to.not.match(/query|content|chatTitle/i)
  })

  it('the actor is never told, whoever else is mentioned', async () => {
    const { mentioned, rows } = build()
    await mentioned([user('bob'), user('carol')])
    expect(rows().map((r) => r.recipientUserIds)).to.deep.equal([['carol']])
    const solo = build()
    await solo.mentioned([user('bob')])
    expect(solo.rows()).to.have.length(0)
    expect(solo.outbox.write.called, 'nothing to write').to.equal(true)
    expect(solo.written[0]).to.deep.equal([])
  })

  it('a user who is not in the chat is not told, even when the client got the mention through', async () => {
    const { mentioned, rows } = build()
    await mentioned([user('dan'), user('carol')])
    expect(rows().map((r) => r.recipientUserIds[0])).to.deep.equal(['carol'])
  })

  it('a member of a chat team counts as a participant, and the org-wide share makes everyone one', async () => {
    const viaTeam = build({ teams: ['T'], members: { T: ['dan'] } })
    await viaTeam.mentioned([user('dan'), user('erin')])
    expect(viaTeam.rows().map((r) => r.recipientUserIds[0])).to.deep.equal(['dan'])
    const wide = build({ teams: [orgWideTeamId(ORG)] })
    await wide.mentioned([user('erin')])
    expect(wide.rows().map((r) => r.recipientUserIds[0])).to.deep.equal(['erin'])
  })

  it('MN-13: a team of 80 including the actor yields at most 50 recipients, without the actor, in one batched write', async () => {
    const members = ['x0', 'x1', 'bob', ...Array.from({ length: 77 }, (_, i) => `y${String(i)}`)]
    const { mentioned, rows, outbox, memberUserIds } = build({ teams: ['T'], members: { T: members } })
    await mentioned([team('T')])
    expect(memberUserIds.firstCall.args[2]).to.deep.equal({ limit: 50 })
    expect(rows()).to.have.length(49)
    expect(rows().flatMap((r) => r.recipientUserIds)).to.not.include('bob')
    expect(outbox.write.calledOnce, 'PERF-07: one insert').to.equal(true)
  })

  it('a team the chat is not shared with is ignored', async () => {
    const { mentioned, rows, memberUserIds } = build({ teams: ['T'], members: { T: ['dan'], U: ['erin'] } })
    await mentioned([team('U')])
    expect(rows()).to.have.length(0)
    expect(memberUserIds.called).to.equal(false)
  })

  it('a user mentioned directly and through a team is told once', async () => {
    const { mentioned, rows } = build({ teams: ['T'], members: { T: ['carol', 'dan'] } })
    await mentioned([team('T'), user('carol')])
    expect(rows().flatMap((r) => r.recipientUserIds).sort()).to.deep.equal(['carol', 'dan'])
  })

  it('redelivery: the same message publishes the same keys, which the unique index drops', async () => {
    const { mentioned, rows } = build()
    await mentioned([user('carol')])
    await mentioned([user('carol')])
    expect(rows().map((r) => r.dedupeKey)).to.deep.equal([`chat.mentioned:${MESSAGE}:carol`, `chat.mentioned:${MESSAGE}:carol`])
  })

  describe('email', () => {
    const open = { smtp: true, shareEmails: true }

    it('is off by default: no preference row means no intent', async () => {
      const { mentioned, rows } = build({ ...open })
      await mentioned([user('carol')])
      expect(rows()[0]).to.not.have.property('emailIntent')
    })

    it('goes out for a direct mention when the recipient turned chatMentioned on, with no chat title, no text and no access level', async () => {
      const { mentioned, rows } = build({ ...open, emailPref: { carol: true } })
      await mentioned([user('carol')])
      expect(rows()[0]!.emailIntent).to.deep.equal({ template: 'chatMentioned', actorName: 'Bob', orgName: 'Acme' })
    })

    it('stays off when the preference is false, SMTP is not configured or the share-email flag is closed', async () => {
      for (const setup of [{ ...open, emailPref: { carol: false } }, { smtp: false, shareEmails: true, emailPref: { carol: true } }, { smtp: true, shareEmails: false, emailPref: { carol: true } }]) {
        const { mentioned, rows } = build(setup)
        await mentioned([user('carol')])
        expect(rows()[0], JSON.stringify(setup)).to.not.have.property('emailIntent')
      }
    })

    it('is for directly mentioned people only: a team-expanded member gets the bell alone', async () => {
      const { mentioned, rows } = build({ ...open, teams: ['T'], members: { T: ['dan'] }, emailPref: { dan: true } })
      await mentioned([team('T')])
      expect(rows()[0]).to.not.have.property('emailIntent')
    })

    it('a failed preference lookup costs the email, not the bell', async () => {
      const b = build({ ...open })
      const failing = new ConversationEventProducers({
        audience: {} as never,
        readState: {} as never,
        preferences: {} as never,
        notifier: { publish: async (events) => void b.written.push(await Promise.resolve([{ type: events[0]!.type } as never])) },
        effects: {} as never,
        recipients: { resolve: async () => ['carol'] },
        emailIntents: { forDirectUsers: () => Promise.reject(new Error('mongo down')) },
        logger: { warn: sinon.stub(), error: sinon.stub() },
      })
      const session = { _id: new Types.ObjectId(SESSION), orgId: new Types.ObjectId(ORG), userId: OWNER, sharedWith: [{ userId: 'carol', accessLevel: 'write' }] } as never
      await failing.mentioned({ session, identity, actorUserId: 'bob', messageId: MESSAGE, mentions: [user('carol')] })
      expect(b.rows()).to.have.length(1)
    })
  })

  describe('MN-14: only a person\'s question or note notifies', () => {
    const carol = [user('carol')]
    it('a bot answer, a tool row or an error row carrying mentions yields none', () => {
      for (const messageType of ['bot_response', 'tool_call', 'error']) {
        expect(notifiableMentions({ messageType, mentions: carol }), messageType).to.deep.equal([])
      }
    })
    it('a token in text is not a mention: only the structured field counts', () => {
      expect(notifiableMentions({ messageType: 'bot_response' })).to.deep.equal([])
      expect(notifiableMentions({ messageType: 'user_query', content: '<@user:carol>' } as never)).to.deep.equal([])
    })
    it('user_query and note rows hand their mentions over', () => {
      expect(notifiableMentions({ messageType: 'user_query', mentions: carol })).to.deep.equal(carol)
      expect(notifiableMentions({ messageType: 'note', mentions: carol })).to.deep.equal(carol)
    })
  })
})
