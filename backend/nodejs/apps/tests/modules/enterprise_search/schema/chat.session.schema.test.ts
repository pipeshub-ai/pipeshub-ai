import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema'
import { ChatSessionMessage } from '../../../../src/modules/enterprise_search/schema/chat.session.message.schema'
import { ChatSessionReadState } from '../../../../src/modules/enterprise_search/schema/chat.session.read-state.schema'
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema'
import {
  SHARED_WITH_MAX,
  OWNERSHIP_HISTORY_MAX,
} from '../../../../src/modules/enterprise_search/constants/constants'

const oid = () => new mongoose.Types.ObjectId()

type IndexSpec = [Record<string, 1 | -1>, Record<string, any> | undefined]
const findIndex = (m: mongoose.Model<any>, keys: Record<string, number>): IndexSpec | undefined =>
  (m.schema.indexes() as IndexSpec[]).find(
    ([k]) => JSON.stringify(k) === JSON.stringify(keys),
  )

const baseSession = () => ({ userId: oid(), orgId: oid(), initiator: oid() })

describe('enterprise_search/schema/chat.session.schema (PH-03 PR-3.4)', () => {
  describe('indexes (PH03-08)', () => {
    it('has the multikey user-branch index with exact keys', () => {
      const idx = findIndex(ChatSession, {
        orgId: 1,
        'sharedWith.userId': 1,
        sessionType: 1,
        isDeleted: 1,
        lastActivityAt: -1,
      })
      expect(idx).to.exist
    })

    it('has the multikey team-branch index with exact keys', () => {
      const idx = findIndex(ChatSession, {
        orgId: 1,
        'sharedWith.teamId': 1,
        sessionType: 1,
        isDeleted: 1,
        lastActivityAt: -1,
      })
      expect(idx).to.exist
    })

    it('never puts two array paths in one index', () => {
      for (const [keys] of ChatSession.schema.indexes() as IndexSpec[]) {
        expect(Object.keys(keys).filter((k) => k.startsWith('sharedWith.'))).to.have.length.lessThan(2)
      }
    })

    it('has the unique partial creationKey index filtered on $type string (DB-09)', () => {
      const idx = findIndex(ChatSession, { orgId: 1, initiator: 1, creationKey: 1 })
      expect(idx).to.exist
      expect(idx![1]).to.include({ unique: true })
      expect(idx![1]!.partialFilterExpression).to.deep.equal({ creationKey: { $type: 'string' } })
    })

    it('keeps the {isShared:1} index', () => {
      expect(findIndex(ChatSession, { isShared: 1 })).to.exist
    })

    it('adds no index on hiddenFor / archivedFor / principalType', () => {
      const keys = (ChatSession.schema.indexes() as IndexSpec[]).flatMap(([k]) => Object.keys(k))
      expect(keys.filter((k) => /hiddenFor|archivedFor|principalType/.test(k))).to.deep.equal([])
    })
  })

  describe('defaults and select:false', () => {
    it('applies defaults for new sessions', () => {
      const doc = new ChatSession(baseSession())
      expect(doc.sharedWith).to.have.length(0)
      expect(doc.aclVersion).to.equal(0)
      expect(doc.rev).to.equal(0)
      expect(doc.settings!.editorsCanInvite).to.equal(false)
      expect(doc.settings!.ownerContentShared).to.equal(false)
      expect(doc.activeRun).to.equal(null)
      expect(doc.ownershipHistory).to.have.length(0)
      expect((doc as any).schemaVersion).to.equal(1)
    })

    it('marks internal fields select:false', () => {
      for (const p of ['archivedFor', 'hiddenFor', 'creationKey', 'schemaVersion']) {
        expect((ChatSession.schema.path(p) as any).options.select, p).to.equal(false)
      }
    })

    it('does not define contentPolicy (superseded by D3v2)', () => {
      expect(ChatSession.schema.path('contentPolicy')).to.equal(undefined)
    })
  })

  describe('sharedWith collaborators', () => {
    it('keeps legacy {userId, accessLevel} rows valid and strips _id', () => {
      const uid = oid()
      const doc = new ChatSession({ ...baseSession(), sharedWith: [{ userId: uid, accessLevel: 'write' }] })
      expect(doc.validateSync()).to.equal(undefined)
      const row: any = doc.sharedWith![0]
      expect(row.userId.equals(uid)).to.equal(true)
      expect(row.principalType).to.equal(undefined)
      expect(row.toObject()).to.not.have.property('_id')
    })

    it('accepts team rows with a string teamId including all_<orgId>', () => {
      const doc = new ChatSession({
        ...baseSession(),
        sharedWith: [
          { principalType: 'team', teamId: 'all_65f0c0ffee', accessLevel: 'read', addedBy: oid() },
          { principalType: 'team', teamId: '3f2b8c1e-aaaa-bbbb-cccc-1234567890ab', accessLevel: 'write', addedBy: oid() },
        ],
      })
      expect(doc.validateSync()).to.equal(undefined)
      expect((doc.sharedWith![0] as any).teamId).to.equal('all_65f0c0ffee')
    })

    it('rejects an unknown principalType and accessLevel', () => {
      const doc = new ChatSession({
        ...baseSession(),
        sharedWith: [{ principalType: 'group', userId: oid(), accessLevel: 'admin' }],
      })
      const err = doc.validateSync()!
      expect(err.errors['sharedWith.0.principalType']).to.exist
      expect(err.errors['sharedWith.0.accessLevel']).to.exist
    })

    it('defaults accessLevel to read and addedAt to now', () => {
      const doc = new ChatSession({ ...baseSession(), sharedWith: [{ userId: oid() }] })
      expect((doc.sharedWith![0] as any).accessLevel).to.equal('read')
      expect((doc.sharedWith![0] as any).addedAt).to.be.instanceOf(Date)
    })

    it('caps sharedWith at SHARED_WITH_MAX', () => {
      const rows = (n: number) => Array.from({ length: n }, () => ({ userId: oid(), accessLevel: 'read' }))
      expect(new ChatSession({ ...baseSession(), sharedWith: rows(SHARED_WITH_MAX) }).validateSync()).to.equal(undefined)
      const err = new ChatSession({ ...baseSession(), sharedWith: rows(SHARED_WITH_MAX + 1) }).validateSync()
      expect(err!.errors.sharedWith).to.exist
    })
  })

  describe('activeRun and ownershipHistory', () => {
    it('requires runId, userId, startedAt and leaseExpiresAt', () => {
      const err = new ChatSession({ ...baseSession(), activeRun: { instanceId: 'pod-1' } }).validateSync()!
      for (const f of ['runId', 'userId', 'startedAt', 'leaseExpiresAt']) {
        expect(err.errors[`activeRun.${f}`], f).to.exist
      }
    })

    it('accepts a full lease', () => {
      const doc = new ChatSession({
        ...baseSession(),
        activeRun: { runId: 'r1', userId: oid(), startedAt: new Date(), leaseExpiresAt: new Date() },
      })
      expect(doc.validateSync()).to.equal(undefined)
    })

    it('caps ownershipHistory at 20', () => {
      const t = () => ({ fromUserId: oid(), toUserId: oid(), at: new Date() })
      const mk = (n: number) => new ChatSession({ ...baseSession(), ownershipHistory: Array.from({ length: n }, t) })
      expect(mk(OWNERSHIP_HISTORY_MAX).validateSync()).to.equal(undefined)
      expect(mk(OWNERSHIP_HISTORY_MAX + 1).validateSync()!.errors.ownershipHistory).to.exist
    })
  })

  describe('strict mode keeps fields existing code writes', () => {
    it('retains legacy fields on a session', () => {
      const doc = new ChatSession({
        ...baseSession(),
        title: 't',
        isShared: true,
        projectVisibility: 'project',
        agentKey: 'a1',
        sessionType: 'agent',
      })
      const o = doc.toObject()
      for (const k of ['title', 'isShared', 'projectVisibility', 'agentKey', 'sessionType']) {
        expect(o, k).to.have.property(k)
      }
    })
  })
})

describe('enterprise_search/schema/chat.session.message.schema (PH-03 PR-3.4)', () => {
  const baseMsg = () => ({ sessionId: oid(), orgId: oid(), seq: 0, messageType: 'user_query' })

  it('keeps {sessionId, seq} unique', () => {
    const idx = findIndex(ChatSessionMessage, { sessionId: 1, seq: 1 })
    expect(idx![1]).to.include({ unique: true })
  })

  it('has the unique partial author-scoped clientMessageId index (DB-09, F-17)', () => {
    const idx = findIndex(ChatSessionMessage, { sessionId: 1, authorUserId: 1, clientMessageId: 1 })
    expect(idx).to.exist
    expect(idx![1]).to.include({ unique: true })
    expect(idx![1]!.partialFilterExpression).to.deep.equal({ clientMessageId: { $type: 'string' } })
  })

  it('has no unscoped {sessionId, clientMessageId} index', () => {
    expect(findIndex(ChatSessionMessage, { sessionId: 1, clientMessageId: 1 })).to.equal(undefined)
  })

  it('has the partial {orgId, authorUserId} index on $type objectId', () => {
    const idx = findIndex(ChatSessionMessage, { orgId: 1, authorUserId: 1 })
    expect(idx).to.exist
    expect(idx![1]!.partialFilterExpression).to.deep.equal({ authorUserId: { $type: 'objectId' } })
    expect(idx![1]).to.not.have.property('unique')
  })

  it('uses $type, never $exists, in every partial filter', () => {
    for (const m of [ChatSession, ChatSessionMessage, Notifications]) {
      for (const [, opts] of m.schema.indexes() as IndexSpec[]) {
        if (opts?.partialFilterExpression) {
          expect(JSON.stringify(opts.partialFilterExpression)).to.not.contain('$exists')
        }
      }
    }
  })

  it('leaves new fields undefined on a plain message (legacy-compatible, no defaults)', () => {
    const o = new ChatSessionMessage(baseMsg()).toObject()
    for (const k of ['authorUserId', 'requestedBy', 'inReplyTo', 'clientMessageId', 'filesShared', 'shareToolResults', 'runId']) {
      expect(o, k).to.not.have.property(k)
    }
  })

  it('stores authorship, consent and runId fields', () => {
    const author = oid()
    const doc = new ChatSessionMessage({
      ...baseMsg(),
      authorUserId: author,
      clientMessageId: 'c-1',
      filesShared: true,
      shareToolResults: false,
      runId: 'run-1',
      inReplyTo: oid(),
      requestedBy: oid(),
    })
    expect(doc.validateSync()).to.equal(undefined)
    expect(doc.filesShared).to.equal(true)
    expect(doc.shareToolResults).to.equal(false)
    expect(doc.runId).to.equal('run-1')
  })

  it('caps clientMessageId at 64 characters', () => {
    expect(new ChatSessionMessage({ ...baseMsg(), clientMessageId: 'x'.repeat(64) }).validateSync()).to.equal(undefined)
    expect(new ChatSessionMessage({ ...baseMsg(), clientMessageId: 'x'.repeat(65) }).validateSync()!.errors.clientMessageId).to.exist
  })

  it('defaults schemaVersion to 1 and hides it from reads', () => {
    expect((new ChatSessionMessage(baseMsg()) as any).schemaVersion).to.equal(1)
    expect((ChatSessionMessage.schema.path('schemaVersion') as any).options.select).to.equal(false)
  })
})

describe('enterprise_search/schema/chat.session.read-state.schema (74 §4)', () => {
  it('uses the chatSessionReadStates collection with a unique {userId, sessionId} index', () => {
    expect(ChatSessionReadState.collection.collectionName).to.equal('chatSessionReadStates')
    expect(findIndex(ChatSessionReadState, { userId: 1, sessionId: 1 })![1]).to.include({ unique: true })
  })

  it('requires org, user and session; lastReadSeq defaults to -1', () => {
    const err = new ChatSessionReadState({}).validateSync()!
    for (const f of ['orgId', 'userId', 'sessionId']) expect(err.errors[f], f).to.exist
    const ok = new ChatSessionReadState({ orgId: oid(), userId: oid(), sessionId: oid() })
    expect(ok.lastReadSeq).to.equal(-1)
    expect(ok.validateSync()).to.equal(undefined)
  })

  it('has timestamps and no __v', () => {
    expect(ChatSessionReadState.schema.options.timestamps).to.equal(true)
    expect(ChatSessionReadState.schema.options.versionKey).to.equal(false)
  })
})

describe('notification/schema/notification.schema dedupeKey (PH-03 PR-3.4)', () => {
  it('has the unique partial {assignedTo, dedupeKey} index on $type string', () => {
    const idx = findIndex(Notifications, { assignedTo: 1, dedupeKey: 1 })
    expect(idx).to.exist
    expect(idx![1]).to.include({ unique: true })
    expect(idx![1]!.partialFilterExpression).to.deep.equal({ dedupeKey: { $type: 'string' } })
  })

  it('keeps the existing indexes', () => {
    expect(findIndex(Notifications, { orgId: 1, status: 1 })).to.exist
    expect(findIndex(Notifications, { createdAt: 1 })![1]).to.have.property('expireAfterSeconds')
  })

  it('leaves dedupeKey optional with no default', () => {
    const doc = new Notifications({ orgId: oid(), type: 't', assignedTo: oid() })
    expect(doc.validateSync()).to.equal(undefined)
    expect(doc.toObject()).to.not.have.property('dedupeKey')
  })
})

describe('notification/schema/notification.schema coalesceKey (PH-06 PR-6.1)', () => {
  it('has the unique partial {assignedTo, coalesceKey} index limited to unread, using $type', () => {
    const idx = findIndex(Notifications, { assignedTo: 1, coalesceKey: 1 })
    expect(idx).to.exist
    expect(idx![1]).to.include({ unique: true })
    expect(idx![1]!.partialFilterExpression).to.deep.equal({
      coalesceKey: { $type: 'string' },
      status: 'unread',
    })
  })

  it('leaves coalesceKey optional with no default', () => {
    const doc = new Notifications({ orgId: oid(), type: 't', assignedTo: oid() })
    expect(doc.validateSync()).to.equal(undefined)
    expect(doc.toObject()).to.not.have.property('coalesceKey')
  })
})
