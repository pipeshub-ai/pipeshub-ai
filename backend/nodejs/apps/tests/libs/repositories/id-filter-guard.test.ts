import { expect } from 'chai'
import sinon from 'sinon'
import mongoose, { Schema } from 'mongoose'
import {
  ID_FILTER_FIELDS,
  assertIdFilter,
  assertIdPipeline,
  eq,
  guardIdFilters,
  guardedModel,
  idFieldsOf,
  idOperator,
} from '../../../src/libs/repositories/id-filter-guard/mongo'
import { InvalidIdFilterError } from '../../../src/libs/repositories/errors'
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema'
import { CpBindingModel } from '../../../src/libs/repositories/control-plane/bindings/schema/bindings.schema'
import { transactionWrites } from '../../../src/libs/repositories/agent-harness/transaction-writes/mongo'

const refused = (fn: () => unknown, field: string) => {
  try {
    fn()
  } catch (error) {
    expect(error).to.be.instanceOf(InvalidIdFilterError)
    expect((error as InvalidIdFilterError).statusCode).to.equal(400)
    expect((error as Error).message).to.equal(`The ${field} given is not a valid identifier`)
    return
  }
  expect.fail(`expected ${field} to be refused`)
}

const refusedAsync = async (promise: Promise<unknown>, field: string) => {
  try {
    await promise
  } catch (error) {
    expect(error, String(error)).to.be.instanceOf(InvalidIdFilterError)
    expect((error as Error).message).to.equal(`The ${field} given is not a valid identifier`)
    return
  }
  expect.fail(`expected ${field} to be refused`)
}

describe('id filter guard: assertIdFilter', () => {
  it('passes strings, null, undefined and a $in of strings', () => {
    assertIdFilter({ orgId: 'o', runId: 'r', sessionId: null, streamId: undefined })
    assertIdFilter({ orgId: 'o', runId: { $in: ['a', 'b'] }, sessionId: { $in: [] } })
  })

  it('passes the operators it does not guard: $expr, status operators, nested paths', () => {
    assertIdFilter({
      orgId: 'o',
      status: { $in: ['queued'] },
      'parent.runId': { $exists: true },
      openInteractions: { $all: ['x'] },
      $expr: { $and: [{ $eq: ['$runId', '$$x'] }] },
    })
  })

  it('refuses an operator object as an id', () => {
    refused(() => assertIdFilter({ orgId: 'o', runId: { $ne: 'x' } }), 'runId')
    refused(() => assertIdFilter({ orgId: { $gt: '' } }), 'orgId')
    refused(() => assertIdFilter({ sessionId: { $regex: '.*' } }), 'sessionId')
    refused(() => assertIdFilter({ owner: { $exists: true } }), 'owner')
    refused(() => assertIdFilter({ installation: { $nin: [] } }), 'installation')
  })

  it('passes an exact match on a string (eq) and refuses one on anything else', () => {
    assertIdFilter({ orgId: eq('o'), runId: eq('r') })
    refused(() => assertIdFilter({ runId: eq({ $ne: 'x' }) }), 'runId')
    refused(() => assertIdFilter({ runId: { $eq: 'a', $ne: 'b' } }), 'runId')
    refused(() => assertIdFilter({ runId: eq(['a']) }), 'runId')
  })

  it('refuses a $in with other keys or non-string members', () => {
    refused(() => assertIdFilter({ runId: { $in: ['a'], $ne: 'b' } }), 'runId')
    refused(() => assertIdFilter({ runId: { $in: [{ $gt: '' }] } }), 'runId')
    refused(() => assertIdFilter({ runId: { $in: [/./] } }), 'runId')
    refused(() => assertIdFilter({ runId: { $in: 'a' } }), 'runId')
  })

  it('refuses arrays, numbers, booleans and driver objects', () => {
    refused(() => assertIdFilter({ runId: ['a'] }), 'runId')
    refused(() => assertIdFilter({ commandId: 1 }), 'commandId')
    refused(() => assertIdFilter({ workspaceId: true }), 'workspaceId')
    refused(() => assertIdFilter({ orgId: new mongoose.Types.ObjectId() }), 'orgId')
  })

  it('looks inside $and, $or and $nor', () => {
    refused(() => assertIdFilter({ $or: [{ runId: 'a' }, { runId: { $ne: 'b' } }] }), 'runId')
    refused(() => assertIdFilter({ $and: [{ $nor: [{ streamId: { $gt: '' } }] }] }), 'streamId')
    assertIdFilter({ $or: [{ runId: 'a' }, { runId: { $in: ['b'] } }] })
  })

  it("passes an operator the repository marks as its own", () => {
    assertIdFilter({ sessionId: idOperator({ $gt: 'cursor' }), owner: idOperator({ $exists: false }) })
  })

  it('checks only the fields it is given', () => {
    assertIdFilter({ runId: { $ne: 'x' } }, new Set(['orgId']))
    assertIdFilter({ runId: { $ne: 'x' } }, new Set())
    assertIdFilter(null)
    assertIdFilter('not a filter')
  })

  it('checks every $match of a pipeline and nothing else', () => {
    assertIdPipeline([{ $match: { orgId: 'o' } }, { $group: { _id: '$runId' } }, 'odd'])
    refused(() => assertIdPipeline([{ $sort: { seq: 1 } }, { $match: { runId: { $ne: null } } }]), 'runId')
  })

  it('never puts the value in the error', () => {
    try {
      assertIdFilter({ runId: { $ne: 'secret-value' } })
    } catch (error) {
      expect(JSON.stringify((error as InvalidIdFilterError).toJSON())).to.not.contain('secret-value')
    }
  })
})

describe('id filter guard: schema plugin', () => {
  const connection = mongoose.createConnection()
  const schema = new Schema(
    { orgId: String, runId: String, owner: { type: Schema.Types.Mixed }, status: String, fence: Number },
    { bufferCommands: false },
  )
  guardIdFilters(schema)
  guardIdFilters(schema)
  const Scratch = connection.model('IdGuardScratch', schema)

  // An unopened connection refuses a query that got past the middleware with its own error.
  const passed = async (promise: Promise<unknown>) => {
    try {
      await promise
    } catch (error) {
      expect((error as Error).message).to.match(/before initial connection is complete/)
      return
    }
    expect.fail('an unopened connection answered')
  }

  after(() => connection.close())

  it('guards only the id fields the schema declares as strings', () => {
    expect([...idFieldsOf(schema)].sort()).to.deep.equal(['orgId', 'runId'])
    expect(idFieldsOf(new Schema({}))).to.equal(ID_FILTER_FIELDS)
  })

  it('refuses $ne as runId on every filtered operation', async () => {
    const bad = { orgId: 'o', runId: { $ne: 'x' } }
    await refusedAsync(Scratch.find(bad).exec(), 'runId')
    await refusedAsync(Scratch.findOne(bad).exec(), 'runId')
    await refusedAsync(Scratch.countDocuments(bad).exec(), 'runId')
    await refusedAsync(Scratch.distinct('status', bad).exec(), 'runId')
    await refusedAsync(Scratch.findOneAndUpdate(bad, { $set: { status: 'x' } }).exec(), 'runId')
    await refusedAsync(Scratch.updateOne(bad, { $set: { status: 'x' } }).exec(), 'runId')
    await refusedAsync(Scratch.updateMany(bad, { $set: { status: 'x' } }).exec(), 'runId')
    await refusedAsync(Scratch.deleteOne(bad).exec(), 'runId')
    await refusedAsync(Scratch.deleteMany(bad).exec(), 'runId')
    await refusedAsync(Scratch.findOneAndDelete(bad).exec(), 'runId')
    await refusedAsync(Scratch.replaceOne(bad, { status: 'x' }).exec(), 'runId')
    await refusedAsync(Scratch.aggregate([{ $match: bad }]).exec(), 'runId')
    await refusedAsync(Scratch.bulkWrite([{ updateOne: { filter: bad, update: { $set: { status: 'x' } } } }]), 'runId')
    await refusedAsync(Scratch.bulkWrite([{ deleteMany: { filter: bad } }]), 'runId')
  })

  it('lets claim- and renew-shaped filters, $in and marked operators through', async () => {
    const claim = { orgId: 'o', runId: 'r', $expr: { $and: [{ $eq: ['$status', 'queued'] }] } }
    const renew = { orgId: 'o', runId: 'r', status: { $in: ['running'] }, fence: 3, $expr: { $gt: ['$fence', 0] } }
    await passed(Scratch.findOneAndUpdate(claim, [{ $set: { status: 'running' } }], { updatePipeline: true }).exec())
    await passed(Scratch.findOneAndUpdate(renew, { $set: { status: 'running' } }).exec())
    await passed(Scratch.find({ orgId: 'o', runId: { $in: ['a', 'b'] } }).exec())
    await passed(Scratch.find({ orgId: 'o', runId: idOperator({ $lt: 'cursor' }) }).exec())
    await passed(Scratch.find({ $or: [{ runId: idOperator({ $gt: 'c' }) }] }).exec())
    await passed(Scratch.aggregate([{ $match: { orgId: 'o', runId: idOperator({ $ne: null }) } }]).exec())
    await passed(Scratch.bulkWrite([{ insertOne: { document: { orgId: 'o' } } }, { updateOne: { filter: { orgId: 'o' }, update: { $set: { status: 'x' } } } }]))
    // `owner` is Mixed in this schema: not an id field here.
    await passed(Scratch.find({ owner: { $exists: true } }).exec())
  })

  it('a guarded model is registered once', () => {
    const name = 'IdGuardGuardedModel'
    const first = guardedModel(name, new Schema({ orgId: String }))
    expect(guardedModel(name, new Schema({ orgId: String }))).to.equal(first)
    mongoose.deleteModel(name)
  })
})

describe('id filter guard: harness and control-plane stores', () => {
  afterEach(() => sinon.restore())

  it('the run and binding models guard their ids', async () => {
    expect(idFieldsOf(AgentRunModel.schema).has('runId')).to.equal(true)
    expect(idFieldsOf(AgentRunModel.schema).has('attemptId')).to.equal(true)
    expect(idFieldsOf(CpBindingModel.schema).has('installation')).to.equal(true)
    expect(idFieldsOf(CpBindingModel.schema).has('owner')).to.equal(true)
    await refusedAsync(AgentRunModel.findOne({ orgId: 'o', runId: { $ne: 'x' } }).exec(), 'runId')
    await refusedAsync(CpBindingModel.find({ orgId: 'o', owner: { $gt: '' } }).exec(), 'owner')
  })

  it('a renew-shaped update of a run reaches the driver', async () => {
    const reached = sinon.stub(AgentRunModel.collection, 'findOneAndUpdate').resolves(null as never)
    await AgentRunModel.findOneAndUpdate(
      { orgId: 'o', runId: 'r', status: { $in: ['running'] }, workerId: 'w', attemptId: 'a', fence: 1, $expr: { $gt: ['$fence', 0] } },
      { $set: { leaseExpiresAt: 1 } },
    ).exec()
    expect(reached.calledOnce).to.equal(true)
  })

  it('a queued transaction update is checked before it is queued', async () => {
    const write = sinon.stub().resolves()
    await refusedAsync(
      transactionWrites.update(null, AgentRunModel, { name: 'updateOne', filter: { orgId: 'o', runId: { $ne: 'x' } }, update: { $set: {} } }, write),
      'runId',
    )
    expect(write.called).to.equal(false)
    await transactionWrites.update(null, AgentRunModel, { name: 'updateOne', filter: { orgId: 'o', runId: 'r' }, update: { $set: {} } }, write)
    expect(write.calledOnce).to.equal(true)
  })
})
