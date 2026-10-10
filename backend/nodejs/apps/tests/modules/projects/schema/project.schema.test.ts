import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import { Project } from '../../../../src/modules/projects/schema/project.schema'

describe('project schema: display mirror of the knowledge scope', () => {
  const project = (node: Record<string, unknown>) =>
    new Project({
      orgId: new mongoose.Types.ObjectId(),
      userId: new mongoose.Types.ObjectId(),
      name: 'Scoped',
      appliedFilters: { records: [node] },
    })

  it('stores an entry whose connector name is not known', () => {
    const doc = project({ id: 'folder-1', name: 'A', nodeType: 'folder', connector: '' })
    expect(doc.validateSync()).to.equal(undefined)
    expect(doc.appliedFilters?.records?.[0]?.connector).to.equal('')
  })

  it('still requires a name and a node type', () => {
    const error = project({ id: 'folder-1', name: '', nodeType: '', connector: 'KB' }).validateSync()
    expect(Object.keys(error?.errors ?? {})).to.have.members([
      'appliedFilters.records.0.name',
      'appliedFilters.records.0.nodeType',
    ])
  })
})
