import sinon from 'sinon'
import { Container } from 'inversify'
import { RequestHandler } from 'express'
import { Types } from 'mongoose'
import { IAuditWriter, AuditEventInput } from '../../../../src/libs/audit/audit.writer'
import { ChatAccessLoader } from '../../../../src/modules/authz/loaders/chat.loader'
import { bindCollaboration } from '../../../../src/modules/enterprise_search/container/collaboration.bindings'
import { COLLAB_TYPES } from '../../../../src/modules/enterprise_search/services/collaboration/collab.types'
import { CollaborationEvent } from '../../../../src/modules/enterprise_search/services/collaboration/notify/events'
import { ICollaborationNotifier, PublishOptions } from '../../../../src/modules/enterprise_search/services/collaboration/notify/collaboration-notifier'
import {
  AddOutcome,
  ChangeLevelOutcome,
  ICollaboratorRepository,
  LeaveOutcome,
  RemoveOutcome,
  SessionScope,
  SettingsOutcome,
  TransferOutcome,
} from '../../../../src/modules/enterprise_search/services/collaboration/persistence/collaborator.repository'
import { IReadStateRepository } from '../../../../src/modules/enterprise_search/services/collaboration/persistence/read-state.repository'
import { MongoConversationMessageFeed } from '../../../../src/modules/enterprise_search/services/collaboration/persistence/message-feed'
import { IAgentReadinessPort } from '../../../../src/modules/enterprise_search/services/collaboration/readiness/agent-readiness.port'
import { IAgentDirectory, IAgentProfiles } from '../../../../src/modules/enterprise_search/services/collaboration/mentions/agent.directory'
import { MentionValidator } from '../../../../src/modules/enterprise_search/services/collaboration/mentions/mention.validator'
import { Principal } from '../../../../src/modules/enterprise_search/services/collaboration/domain/types'
import { COLLAB_FLAG_KEYS } from '../../../../src/modules/configuration_manager/constants/constants'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { SHARED_WITH_MAX } from '../../../../src/modules/enterprise_search/constants/constants'

export interface WorldUser {
  displayName: string
  /** Absent: the world's org. */
  orgId?: Types.ObjectId
  kind?: 'human' | 'service'
  isDisabled?: boolean
}

export interface WorldTeam {
  name: string
  /** False: the directory no longer has the team. */
  exists?: boolean
}

export interface CollaborationWorldOptions {
  collab: boolean
  orgId: Types.ObjectId
  orgWideWrite?: boolean
  smtp?: boolean
  shareEmails?: boolean
  /** Users that exist in the org; everyone else is unknown to the directory. */
  users?: Record<string, WorldUser>
  teams?: Record<string, WorldTeam>
  teamsOf?: (userId: string) => string[]
  /** Active members of a team, as `memberUserIds` answers; a team not listed is unresolved. */
  teamMembers?: Record<string, string[]>
  /** Mentions flag and the agent directory behind the mention validator (PH-10.4). */
  mentions?: boolean
  agents?: IAgentDirectory
  /** The agent builder flag and the agent names behind the @ picker; its access checks are `agents`. */
  agentBuilder?: boolean
  agentProfiles?: IAgentProfiles
  /** Project ids a user may open, by user. */
  projectAccess?: (userId: string, projectId: string) => boolean
  readiness?: IAgentReadinessPort
}

const oid = (id: unknown): string => String(id)

/** The repository's contract applied to the in-memory store, for suites that cannot run `$expr` pipeline updates. */
export class InMemoryCollaboratorRepository implements ICollaboratorRepository {
  /** Reads and writes through the `ChatSession` statics the in-memory store has stubbed. */
  private async find(scope: SessionScope) {
    const doc = await ChatSession.findOne({ _id: new Types.ObjectId(scope.sessionId) })
    if (!doc || oid(doc.get('orgId')) !== scope.orgId) return undefined
    if (scope.ownerId !== undefined && oid(doc.get('userId')) !== scope.ownerId) return undefined
    return doc
  }

  private rows(doc: Awaited<ReturnType<InMemoryCollaboratorRepository['find']>>): Array<Record<string, unknown>> {
    return ((doc?.toObject().sharedWith ?? []) as Array<Record<string, unknown>>).map((r) => ({ ...r }))
  }

  private matches(row: Record<string, unknown>, p: Principal): boolean {
    return p.type === 'user' ? oid(row.userId) === p.userId : row.teamId === p.teamId
  }

  private stamp(doc: NonNullable<Awaited<ReturnType<InMemoryCollaboratorRepository['find']>>>) {
    doc.set('rev', ((doc.get('rev') as number | undefined) ?? 0) + 1)
    doc.set('aclVersion', ((doc.get('aclVersion') as number | undefined) ?? 0) + 1)
    doc.set('isShared', this.rows(doc).length > 0)
    return { rev: doc.get('rev') as number, aclVersion: doc.get('aclVersion') as number, isShared: doc.get('isShared') as boolean }
  }

  async add(scope: SessionScope, input: Parameters<ICollaboratorRepository['add']>[1]): Promise<AddOutcome> {
    const doc = await this.find(scope)
    if (!doc) return { status: 'not_found' }
    const rows = this.rows(doc)
    if (rows.some((r) => this.matches(r, input.principal))) return { status: 'already_present' }
    if (rows.length >= SHARED_WITH_MAX) return { status: 'limit', max: SHARED_WITH_MAX }
    const { principal } = input
    rows.push({
      principalType: principal.type,
      ...(principal.type === 'user' ? { userId: new Types.ObjectId(principal.userId) } : { teamId: principal.teamId }),
      accessLevel: input.accessLevel,
      addedBy: new Types.ObjectId(input.addedBy),
    })
    doc.set('sharedWith', rows)
    if (principal.type === 'user') {
      const keep = (list: unknown) => ((list ?? []) as unknown[]).filter((id) => oid(id) !== principal.userId)
      doc.set('hiddenFor', keep(doc.get('hiddenFor')))
      doc.set('archivedFor', keep(doc.get('archivedFor')))
    }
    return { status: 'added', ...this.stamp(doc) }
  }

  async changeLevel(scope: SessionScope, principal: Principal, accessLevel: 'read' | 'write'): Promise<ChangeLevelOutcome> {
    const doc = await this.find(scope)
    if (!doc) return { status: 'not_found' }
    const rows = this.rows(doc)
    const row = rows.find((r) => this.matches(r, principal))
    if (!row) return { status: 'absent' }
    if (row.accessLevel === accessLevel) return { status: 'unchanged' }
    row.accessLevel = accessLevel
    doc.set('sharedWith', rows)
    return { status: 'applied', ...this.stamp(doc) }
  }

  async remove(scope: SessionScope, principal: Principal): Promise<RemoveOutcome> {
    const doc = await this.find(scope)
    if (!doc) return { status: 'not_found' }
    const rows = this.rows(doc)
    if (!rows.some((r) => this.matches(r, principal))) return { status: 'absent' }
    doc.set('sharedWith', rows.filter((r) => !this.matches(r, principal)))
    return { status: 'applied', ...this.stamp(doc) }
  }

  async transfer(scope: Omit<SessionScope, 'ownerId'>, input: { fromUserId: string; toUserId: string }): Promise<TransferOutcome> {
    const doc = await this.find({ ...scope, ownerId: input.fromUserId })
    const rows = doc ? this.rows(doc) : []
    if (!doc || !rows.some((r) => oid(r.userId) === input.toUserId && r.accessLevel === 'write')) return { status: 'no_match' }
    doc.set('sharedWith', [
      ...rows.filter((r) => oid(r.userId) !== input.toUserId),
      { principalType: 'user', userId: new Types.ObjectId(input.fromUserId), accessLevel: 'write', addedBy: new Types.ObjectId(input.fromUserId) },
    ])
    doc.set('userId', new Types.ObjectId(input.toUserId))
    doc.set('initiator', new Types.ObjectId(input.toUserId))
    const history = (doc.get('ownershipHistory') ?? []) as unknown[]
    doc.set('ownershipHistory', [...history, { fromUserId: new Types.ObjectId(input.fromUserId), toUserId: new Types.ObjectId(input.toUserId), at: new Date() }].slice(-20))
    return { status: 'applied', ...this.stamp(doc) }
  }

  async updateSettings(scope: SessionScope, patch: { editorsCanInvite?: boolean; ownerContentShared?: boolean; respondMode?: string }): Promise<SettingsOutcome> {
    const doc = await this.find(scope)
    if (!doc) return { status: 'not_found' }
    if (Object.keys(patch).length === 0) return { status: 'unchanged' }
    for (const [key, value] of Object.entries(patch)) doc.set(`settings.${key}`, value)
    return { status: 'applied', ...this.stamp(doc) }
  }

  async leave(scope: Omit<SessionScope, 'ownerId'>, userId: string): Promise<LeaveOutcome> {
    const doc = await this.find(scope)
    if (!doc) return { status: 'not_found' }
    if (oid(doc.get('userId')) === userId) return { status: 'owner' }
    doc.set('sharedWith', this.rows(doc).filter((r) => oid(r.userId) !== userId))
    doc.set('hiddenFor', [...((doc.get('hiddenFor') ?? []) as unknown[]), new Types.ObjectId(userId)])
    return { status: 'applied', ...this.stamp(doc) }
  }
}

export class RecordingAudit implements IAuditWriter {
  readonly events: AuditEventInput[] = []
  failWith: Error | undefined
  async record(e: AuditEventInput): Promise<void> {
    if (this.failWith) throw this.failWith
    this.events.push(e)
  }
}

export class RecordingNotifier implements ICollaborationNotifier {
  readonly batches: CollaborationEvent[][] = []
  readonly options: Array<PublishOptions | undefined> = []
  failWith: Error | undefined
  get events(): CollaborationEvent[] {
    return this.batches.flat()
  }
  async publish(events: readonly CollaborationEvent[], opts?: PublishOptions): Promise<void> {
    if (this.failWith) throw this.failWith
    this.batches.push([...events])
    this.options.push(opts)
  }
}

export class FakeReadState implements IReadStateRepository {
  readonly marks: Array<{ userId: string; sessionId: string; seq: number; minIntervalMs?: number }> = []
  readonly lastRead = new Map<string, number>()
  queries = 0
  async markRead(_orgId: string, userId: string, sessionId: string, seq: number, opts?: { minIntervalMs?: number }): Promise<void> {
    this.marks.push({ userId, sessionId, seq, minIntervalMs: opts?.minIntervalMs })
    const key = `${userId}:${sessionId}`
    this.lastRead.set(key, Math.max(this.lastRead.get(key) ?? -1, seq))
  }
  async lastReadSeqs(userId: string, sessionIds: readonly string[]): Promise<ReadonlyMap<string, number>> {
    this.queries += 1
    return new Map(sessionIds.flatMap((id) => (this.lastRead.has(`${userId}:${id}`) ? [[id, this.lastRead.get(`${userId}:${id}`)!] as const] : [])))
  }
}

export interface CollaborationWorld {
  audit: RecordingAudit
  notifier: RecordingNotifier
  readState: FakeReadState
  flags: { isEnabled: sinon.SinonStub }
  mentionValidator: MentionValidator
  setFlag(key: string, value: boolean): void
}

/** Binds the production collaboration wiring over fakes of everything outside the process. */
export function bindCollaborationWorld(container: Container, options: CollaborationWorldOptions): CollaborationWorld {
  const flagValues = new Map<string, boolean>([
    [COLLAB_FLAG_KEYS.collaborativeChats, options.collab],
    [COLLAB_FLAG_KEYS.orgWideChatWrite, options.orgWideWrite ?? false],
    [COLLAB_FLAG_KEYS.chatShareEmails, options.shareEmails ?? true],
    [COLLAB_FLAG_KEYS.chatMentions, options.mentions ?? false],
    [COLLAB_FLAG_KEYS.chatAgentBuilder, options.agentBuilder ?? false],
  ])
  const flags = { isEnabled: sinon.stub().callsFake(async (key: string) => flagValues.get(key) ?? false) }
  const audit = new RecordingAudit()
  const notifier = new RecordingNotifier()
  const readState = new FakeReadState()
  const users = options.users ?? {}
  const teams = options.teams ?? {}
  const directoryUsers = {
    displayNames: async (_org: string, ids: readonly string[]) => new Map(ids.map((id) => [id, users[id]?.displayName ?? ''])),
    findByIds: async (org: string, ids: readonly string[]) =>
      ids.flatMap((id) =>
        users[id] && String(users[id]!.orgId ?? options.orgId) === org
          ? [{ userId: id, displayName: users[id]!.displayName, kind: users[id]!.kind ?? 'human', isDisabled: users[id]!.isDisabled === true }]
          : [],
      ),
  }
  const directoryTeams = {
    callerTeamIds: async (identity: { userId: string }) => ({ status: 'ok' as const, teamIds: options.teamsOf?.(identity.userId) ?? [] }),
    teamsVersion: async () => 0,
    exists: async (teamId: string) => teams[teamId]?.exists !== false && teamId in teams,
    memberUserIds: async (teamId: string) =>
      options.teamMembers?.[teamId] ? { status: 'ok' as const, userIds: options.teamMembers[teamId]! } : { status: 'unresolved' as const },
  }
  const agentAccess = options.agents ?? { canExecute: async () => false, isServiceAccount: async () => false }
  const mentionValidator = new MentionValidator({
    users: directoryUsers,
    teams: directoryTeams,
    agents: agentAccess,
  })
  bindCollaboration(container, {
    mentionValidator,
    agentDirectory: options.agentProfiles && { canExecute: (i, k) => agentAccess.canExecute(i, k), isServiceAccount: (i, k) => agentAccess.isServiceAccount(i, k), describe: options.agentProfiles.describe },
    appConfig: { iamBackend: 'http://iam.test', connectorBackend: 'http://connectors.test' } as never,
    keyValueStore: {} as never,
    flags,
    chats: new ChatAccessLoader(),
    projects: {
      accessibleProjectIds: async () => [],
      roleOf: async (subject, projectId) => (options.projectAccess?.(subject.userId, projectId) === false ? null : { role: 'viewer', project: {} as never }),
      assertAtLeast: async () => ({}) as never,
    },
    teams: directoryTeams,
    users: directoryUsers,
    readiness: options.readiness ?? { check: async () => ({ status: 'ready' }), invalidate: () => undefined },
    repo: new InMemoryCollaboratorRepository(),
    audit,
    readState,
    preferences: { get: async () => ({ email: { chatShared: true, ownershipTransferred: true }, inApp: { chatActivity: true }, mutedSessions: [] }) } as never,
    messages: new MongoConversationMessageFeed(),
    notifier,
    logger: { info: () => undefined, warn: () => undefined, error: () => undefined } as never,
    teamLookup: {
      describeMany: async (ids) =>
        new Map(ids.map((id) => [id, teams[id] === undefined || teams[id]!.exists === false ? { status: 'missing' as const } : { status: 'ok' as const, name: teams[id]!.name }])),
    },
    orgs: { displayName: async () => 'Acme' },
    smtpConfigured: async () => options.smtp ?? false,
  })
  return { audit, notifier, readState, flags, mentionValidator, setFlag: (key, value) => void flagValues.set(key, value) }
}

const noop: RequestHandler = (_req, res) => void res.status(200).end()

/** For route-table suites that only read the router stack: the bindings exist, nothing behind them runs. */
export function bindCollaborationStubs(container: Container): void {
  container.bind(COLLAB_TYPES.FeatureFlags).toConstantValue({ isEnabled: async () => true })
  container.bind(COLLAB_TYPES.CollaborationService).toConstantValue({
    legacyShare: () => Promise.reject(new Error('stub')),
    legacyUnshare: () => Promise.reject(new Error('stub')),
  })
  container.bind(COLLAB_TYPES.CollaboratorsController).toConstantValue({
    list: noop,
    upsert: noop,
    remove: noop,
    updateSettings: noop,
    transferOwnership: noop,
    leave: noop,
    getFeed: noop,
    getReadiness: noop,
  })
  container.bind(COLLAB_TYPES.MentionsController).toConstantValue({ list: noop, postNote: () => noop })
  container.bind(COLLAB_TYPES.AuditWriter).toConstantValue({ record: async () => undefined })
}
