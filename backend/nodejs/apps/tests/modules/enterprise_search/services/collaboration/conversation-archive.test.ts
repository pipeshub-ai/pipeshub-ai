import { expect } from 'chai'
import { Types } from 'mongoose'
import {
  archiveStateOf,
  perUserArchiveUpdate,
} from '../../../../../src/modules/enterprise_search/services/collaboration/access/conversation-archive'

const owner = new Types.ObjectId()
const other = new Types.ObjectId()
const facts = (over: Record<string, unknown> = {}) => ({ userId: owner, ...over }) as never

describe('conversation archive state', () => {
  it('an unshared chat keeps the global flag, with the flag on or off', () => {
    for (const collab of [true, false]) {
      expect(archiveStateOf(facts({ isArchived: true }), String(owner), collab)).to.deep.equal({ perUser: false, archived: true })
      expect(archiveStateOf(facts({ archivedFor: [owner] }), String(owner), collab)).to.deep.equal({ perUser: false, archived: false })
    }
  })

  it('a shared chat is per user with the flag on, for the owner too, and global with it off', () => {
    const shared = { sharedWith: [{ userId: other, accessLevel: 'read' }], archivedFor: [other] }
    expect(archiveStateOf(facts(shared), String(other), true)).to.deep.equal({ perUser: true, archived: true })
    expect(archiveStateOf(facts(shared), String(owner), true)).to.deep.equal({ perUser: true, archived: false })
    expect(archiveStateOf(facts(shared), String(other), false)).to.deep.equal({ perUser: false, archived: false })
  })

  it('a project-visible chat without recipients is per user', () => {
    expect(archiveStateOf(facts({ projectVisibility: 'project' }), String(other), true).perUser).to.equal(true)
    expect(archiveStateOf(facts({ projectVisibility: 'private' }), String(other), true).perUser).to.equal(false)
  })

  it('an owner archive made before the first share still reads as archived for the owner only', () => {
    const shared = facts({ sharedWith: [{ userId: other, accessLevel: 'read' }], isArchived: true })
    expect(archiveStateOf(shared, String(owner), true).archived).to.equal(true)
    expect(archiveStateOf(shared, String(other), true).archived).to.equal(false)
  })

  it('updates touch archivedFor only: no rev, no lastActivityAt', () => {
    expect(perUserArchiveUpdate('archive', facts(), String(other))).to.deep.equal({ $addToSet: { archivedFor: other } })
    expect(perUserArchiveUpdate('unarchive', facts(), String(other))).to.deep.equal({ $pull: { archivedFor: other } })
    expect(perUserArchiveUpdate('unarchive', facts({ isArchived: true }), String(owner))).to.deep.equal({
      $pull: { archivedFor: owner },
      $set: { isArchived: false, archivedBy: null },
    })
  })
})
