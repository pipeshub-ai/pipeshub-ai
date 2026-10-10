import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import { Project } from '../../../../src/modules/projects/schema/project.schema'

const oid = () => new mongoose.Types.ObjectId()
const base = () => ({ orgId: oid(), userId: oid(), name: 'p' })

describe('projects/schema/project.schema ACL fields (PH-03 PR-3.4)', () => {
  it('defaults aclVersion to 0 and projectChatAccess to viewer', () => {
    const doc = new Project(base())
    expect(doc.aclVersion).to.equal(0)
    expect(doc.projectChatAccess).to.equal('viewer')
  })

  it('accepts editor and rejects other ceilings', () => {
    expect(new Project({ ...base(), projectChatAccess: 'editor' }).validateSync()).to.equal(undefined)
    expect(new Project({ ...base(), projectChatAccess: 'owner' }).validateSync()!.errors.projectChatAccess).to.exist
  })

  it('keeps existing member fields under strict mode', () => {
    const uid = oid()
    const doc = new Project({ ...base(), members: [{ principalType: 'user', principalId: uid, role: 'editor', addedBy: oid() }] })
    expect(doc.validateSync()).to.equal(undefined)
    expect(doc.toObject().members[0]).to.include.keys('principalType', 'principalId', 'role')
  })
})
