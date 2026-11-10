import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { commitWithin, mongoDrainRequested } from '../../../src/libs/repositories/mongo'

const withDb = (db: unknown) => Object.defineProperty(mongoose.connection, 'db', { value: db, configurable: true })

describe('mongoDrainRequested', () => {
  afterEach(() => { delete (mongoose.connection as any).db })

  // `draining` stops new turns so those in flight can finish; `readonly` and `retired` refuse the turn's writes, so the turn must be refused up front too
  for (const [mode, expected] of [['active', false], ['draining', true], ['readonly', true], ['retired', true], [undefined, false]] as const)
    it(`${mode ?? 'no fence document'} -> ${expected}`, async () => {
      withDb({ collection: () => ({ findOne: async () => (mode ? { _id: 'fence', mode } : null) }) })
      expect(await mongoDrainRequested()).to.equal(expected)
    })

  it('sees a fence that left active a moment ago: the very next admission is refused', async () => {
    let mode = 'active'
    withDb({ collection: () => ({ findOne: async () => ({ _id: 'fence', mode }) }) })
    expect(await mongoDrainRequested()).to.equal(false)
    mode = 'draining'
    expect(await mongoDrainRequested()).to.equal(true)
  })
})

describe('commitWithin', () => {
  afterEach(() => { delete (mongoose.connection as any).db })

  it('kills the stalled session by its id document, `{ id: Binary }`, with a deadline', async () => {
    const command = sinon.stub().resolves({ ok: 1 })
    withDb({ admin: () => ({ command }) })
    const lsid = { id: { fake: 'binary' } }
    const session = { commitTransaction: () => new Promise<void>(() => undefined), id: lsid }
    let thrown: unknown
    await commitWithin(session as never, 5).catch((e) => { thrown = e })
    expect(thrown).to.be.instanceOf(Error)
    expect(command.firstCall.args[0]).to.deep.equal({ killSessions: [lsid] })
    expect(command.firstCall.args[1]).to.have.property('timeoutMS')
  })
})
