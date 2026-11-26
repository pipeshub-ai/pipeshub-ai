import { expect } from 'chai'
import sinon from 'sinon'
import type { ClientSession } from 'mongoose'
import { InvalidIdFilterError } from '../../../src/libs/repositories/errors'
import { AgentWorkspaceHeadModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema'
import {
  moveWorkspaceHead,
  type WorkspaceHeadMove,
} from '../../../src/libs/repositories/agent-harness/workspace-head-cas/mongo'
import { moveWorkspaceHeadIn } from '../../../src/libs/repositories/agent-harness/workspace-head-cas/pg'
import type { PgKernelTx } from '../../../src/libs/repositories/agent-harness/pg-documents/pg'

const session = {} as ClientSession
const base: WorkspaceHeadMove = { orgId: 'o1', workspaceId: 'w1', expectedRevision: 0, revision: 1, runId: 'r1' }

describe('workspace head CAS: the move is rebuilt from checked fields (CodeQL js/sql-injection #1619)', () => {
  let insert: sinon.SinonStub
  let update: sinon.SinonStub
  let query: sinon.SinonStub
  const pg = (): PgKernelTx => ({ query } as unknown as PgKernelTx)

  beforeEach(() => {
    insert = sinon.stub(AgentWorkspaceHeadModel, 'findOneAndUpdate').returns({ lean: async () => null } as never)
    update = sinon.stub(AgentWorkspaceHeadModel, 'updateOne').resolves({ modifiedCount: 1 } as never)
    query = sinon.stub().resolves({ rowCount: 1 })
  })
  afterEach(() => sinon.restore())

  const refused = async (move: unknown, field: string) => {
    for (const run of [
      () => moveWorkspaceHead(move as WorkspaceHeadMove, 5, session),
      () => moveWorkspaceHeadIn(pg(), move as WorkspaceHeadMove, 5),
    ]) {
      try {
        await run()
        expect.fail(`expected ${field} to be refused`)
      } catch (error) {
        expect(error, String(error)).to.be.instanceOf(InvalidIdFilterError)
        expect((error as Error).message).to.equal(`The ${field} given is not a valid identifier`)
      }
    }
    expect(insert.called || update.called || query.called, 'a refused move reaches no driver').to.equal(false)
  }

  it('refuses an operator-shaped onCreate before any driver call', async () => {
    await refused({ ...base, onCreate: { forkOf: { workspaceId: { $gt: '' }, revision: 1 }, mergeBase: 0 } }, 'onCreate.forkOf.workspaceId')
    await refused({ ...base, onCreate: { forkOf: { $gt: '' }, mergeBase: 0 } }, 'onCreate.forkOf.workspaceId')
    await refused({ ...base, onCreate: { forkOf: null, mergeBase: 0 } }, 'onCreate.forkOf')
    await refused({ ...base, onCreate: { forkOf: { workspaceId: 'w0', revision: 1 }, mergeBase: { $gt: 0 } } }, 'onCreate.mergeBase')
  })

  it('refuses a revision that is not a non-negative safe integer, and ids that are not strings', async () => {
    await refused({ ...base, revision: 1.5 }, 'revision')
    await refused({ ...base, revision: -1 }, 'revision')
    await refused({ ...base, revision: Number.MAX_SAFE_INTEGER + 1 }, 'revision')
    await refused({ ...base, expectedRevision: '0' }, 'expectedRevision')
    await refused({ ...base, expectedRevision: { $gte: 0 } }, 'expectedRevision')
    await refused({ ...base, workspaceId: { $ne: null } }, 'workspaceId')
    await refused({ ...base, orgId: ['o1'] }, 'orgId')
    await refused({ ...base, runId: 7 }, 'runId')
    await refused({ ...base, onCreate: { forkOf: { workspaceId: 'w0', revision: 0.5 }, mergeBase: 0 } }, 'onCreate.forkOf.revision')
  })

  it('writes exactly the named fields of a create: an extra key in onCreate is dropped', async () => {
    const move = {
      ...base,
      onCreate: { forkOf: { workspaceId: 'w0', revision: 3, extra: 'x' }, mergeBase: 2, $where: 'x', orgId: 'o2' },
    } as unknown as WorkspaceHeadMove
    expect(await moveWorkspaceHead(move, 5, session)).to.equal(true)
    const [filter, change] = insert.firstCall.args
    expect(filter).to.deep.equal({ orgId: { $eq: 'o1' }, workspaceId: { $eq: 'w1' } })
    expect(change).to.deep.equal({
      $setOnInsert: { revision: 1, runId: 'r1', updatedAt: 5, forkOf: { workspaceId: 'w0', revision: 3 }, mergeBase: 2 },
    })
    expect(await moveWorkspaceHeadIn(pg(), move, 5)).to.equal(true)
    const doc = JSON.parse(String(query.firstCall.args[1][2]))
    expect(doc).to.deep.include({ orgId: 'o1', workspaceId: 'w1', forkOf: { workspaceId: 'w0', revision: 3 }, mergeBase: 2 })
    expect(Object.keys(doc).sort()).to.deep.equal(['forkOf', 'mergeBase', 'orgId', 'revision', 'runId', 'updatedAt', 'workspaceId'])
  })

  it('moves an existing head with eq filters and a fixed $set', async () => {
    expect(await moveWorkspaceHead({ ...base, expectedRevision: 4, revision: 5 }, 9, session)).to.equal(true)
    const [filter, change] = update.firstCall.args
    expect(filter).to.deep.equal({ orgId: { $eq: 'o1' }, workspaceId: { $eq: 'w1' }, revision: { $eq: 4 } })
    expect(change).to.deep.equal({ $set: { revision: 5, runId: 'r1', updatedAt: 9 } })
  })
})
