import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import { Project } from '../../../../src/modules/projects/schema/project.schema'

const oid = () => new mongoose.Types.ObjectId()
const base = () => ({ orgId: oid(), userId: oid(), name: 'p' })

describe('projects/schema/project.schema members', () => {
  it('keeps existing member fields under strict mode', () => {
    const uid = oid()
    const doc = new Project({ ...base(), members: [{ principalType: 'user', principalId: uid, role: 'editor', addedBy: oid() }] })
    expect(doc.validateSync()).to.equal(undefined)
    expect(doc.toObject().members[0]).to.include.keys('principalType', 'principalId', 'role')
  })
})
