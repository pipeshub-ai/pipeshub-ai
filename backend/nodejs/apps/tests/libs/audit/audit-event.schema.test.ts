import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import { AuditEvent, AUDIT_RETENTION_SECONDS } from '../../../src/libs/audit/audit-event.schema'

const oid = () => new mongoose.Types.ObjectId()
const idx = (keys: Record<string, number>) =>
  (AuditEvent.schema.indexes() as any[]).find(([k]) => JSON.stringify(k) === JSON.stringify(keys))

describe('libs/audit/audit-event.schema', () => {
  it('uses the auditEvents collection, createdAt only, no __v', () => {
    expect(AuditEvent.collection.collectionName).to.equal('auditEvents')
    expect(AuditEvent.schema.options.versionKey).to.equal(false)
    expect(AuditEvent.schema.options.timestamps).to.deep.equal({ createdAt: true, updatedAt: false })
  })

  it('declares the target, actor and TTL indexes (400 days)', () => {
    expect(idx({ orgId: 1, targetType: 1, targetId: 1, createdAt: -1 })).to.exist
    expect(idx({ orgId: 1, actorUserId: 1, createdAt: -1 })).to.exist
    expect(AUDIT_RETENTION_SECONDS).to.equal(400 * 24 * 3600)
    expect(idx({ createdAt: 1 })[1]).to.include({ expireAfterSeconds: AUDIT_RETENTION_SECONDS })
  })

  it('requires org, actor, action, targetType and targetId', () => {
    const err = new AuditEvent({}).validateSync()!
    for (const f of ['orgId', 'actorUserId', 'action', 'targetType', 'targetId']) {
      expect(err.errors[f], f).to.exist
    }
  })

  it('accepts a full share event with before/after payloads', () => {
    const doc = new AuditEvent({
      orgId: oid(),
      actorUserId: oid(),
      action: 'chat.accessChange',
      targetType: 'chatSession',
      targetId: String(oid()),
      principal: { principalType: 'team', principalId: 'all_abc' },
      before: { accessLevel: 'read' },
      after: { accessLevel: 'write' },
      requestId: 'req-1',
    })
    expect(doc.validateSync()).to.equal(undefined)
    expect((doc.after as any).accessLevel).to.equal('write')
    expect(doc.schemaVersion).to.equal(1)
  })
})
