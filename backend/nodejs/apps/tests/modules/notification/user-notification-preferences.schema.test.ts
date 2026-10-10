import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import {
  UserNotificationPreferences,
  MUTED_SESSIONS_MAX,
} from '../../../src/modules/notification/schema/user-notification-preferences.schema'

const oid = () => new mongoose.Types.ObjectId()

describe('notification/schema/user-notification-preferences.schema', () => {
  it('uses its own collection and a unique {orgId, userId} index', () => {
    expect(UserNotificationPreferences.collection.collectionName).to.equal('userNotificationPreferences')
    const idx = (UserNotificationPreferences.schema.indexes() as any[]).find(
      ([k]) => JSON.stringify(k) === JSON.stringify({ orgId: 1, userId: 1 }),
    )
    expect(idx[1]).to.include({ unique: true })
  })

  it('defaults every channel on and mutedSessions empty', () => {
    const doc = new UserNotificationPreferences({ orgId: oid(), userId: oid() })
    expect(doc.email.chatShared).to.equal(true)
    expect(doc.email.ownershipTransferred).to.equal(true)
    expect(doc.inApp.chatActivity).to.equal(true)
    expect(doc.mutedSessions).to.have.length(0)
    expect(doc.inApp.chatMentioned).to.equal(true)
    expect(doc.email.chatMentioned).to.equal(false)
    expect(doc.tipsSeen).to.have.length(0)
    expect(doc.schemaVersion).to.equal(1)
  })

  it('requires orgId and userId', () => {
    const err = new UserNotificationPreferences({}).validateSync()!
    expect(err.errors.orgId).to.exist
    expect(err.errors.userId).to.exist
  })

  it('caps mutedSessions at 500', () => {
    const mk = (n: number) =>
      new UserNotificationPreferences({ orgId: oid(), userId: oid(), mutedSessions: Array.from({ length: n }, oid) })
    expect(mk(MUTED_SESSIONS_MAX).validateSync()).to.equal(undefined)
    expect(mk(MUTED_SESSIONS_MAX + 1).validateSync()!.errors.mutedSessions).to.exist
  })
})
