/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import {
  DuplicateEnvelope,
  configureEnvelope,
  configureInbox,
  deliveryStepDone,
  recordDeliveryStep,
  deriveKey,
  replayWindowSeconds,
  runWithEnvelope,
  sign,
  withDeliveryRecorded,
} from '../../../src/libs/context/envelope'
import { makeOrgContext } from '../../../src/libs/context/orgContext'
import { MemInbox } from '../../helpers/memory-inbox'

const KEY = deriveKey('inbox-test-secret')
const T = 'notification'
const G = 'notification-consumer-group'
const signed = (orgId = 'org_a', payload: Record<string, unknown> = { orgId }): any =>
  sign(KEY, T, payload, makeOrgContext({ orgId, userId: 'u1', kind: 'user', source: 'jwt' }))

describe('envelope: durable consumer inbox', () => {
  let inbox: MemInbox
  let runs: unknown[]
  const handler = async (v: unknown) => { runs.push(v); return 'done' }
  const deliver = (m: any, group = G, fn: (v: unknown) => Promise<unknown> = handler) =>
    runWithEnvelope(T, JSON.parse(JSON.stringify(m)), fn, group)

  beforeEach(() => {
    inbox = new MemInbox()
    runs = []
    configureEnvelope(async () => [KEY], null, KEY)
    configureInbox(inbox)
  })
  afterEach(() => {
    configureInbox(null)
    configureEnvelope(null)
  })

  it('a signed event delivered twice runs once; the copy is acknowledged as handled', async () => {
    const m = signed()
    expect(await deliver(m)).to.equal('done')
    expect(await deliver(m)).to.equal(undefined)
    expect(runs).to.have.length(1)
    expect(inbox.rows.size).to.equal(1)
  })

  it('a replay that comes back after other events is still skipped', async () => {
    const first = signed()
    await deliver(first)
    await deliver(signed())
    await deliver(first)
    expect(runs).to.have.length(2)
  })

  it('a failed handler records nothing, so its retry runs and then records', async () => {
    const m = signed()
    let fail = true
    const flaky = async (v: unknown) => { runs.push(v); if (fail) throw new Error('store away'); return 'ok' }
    let thrown: unknown
    try { await deliver(m, G, flaky) } catch (e) { thrown = e }
    expect((thrown as Error).message).to.equal('store away')
    expect(inbox.rows.size).to.equal(0)
    fail = false
    expect(await deliver(m, G, flaky)).to.equal('ok')
    expect(await deliver(m, G, flaky)).to.equal(undefined)
    expect(runs).to.have.length(2)
  })

  it('rows are per consumer group', async () => {
    const m = signed('org_a')
    await deliver(m, G)
    await deliver(m, 'mail-consumer-group')
    await deliver(m, 'mail-consumer-group')
    expect(runs).to.have.length(2)
  })

  it('an unsigned flat message is matched by its top-level eventId and orgId', async () => {
    const m = { orgId: 'org_a', type: 'mail.deliveryFailed', eventId: 'evt-1' }
    await deliver(m)
    await deliver(m)
    expect(runs).to.have.length(1)
    expect(await deliver({ ...m, orgId: 'org_b' })).to.equal('done')
    expect(runs).to.have.length(2)
  })

  it('an unsigned message with payload.orgId is matched by its top-level eventId', async () => {
    const m = { eventType: 'x', payload: { orgId: 'org_a' }, eventId: 'evt-2' }
    await deliver(m)
    await deliver(m)
    expect(runs).to.have.length(1)
  })

  it('a message without an event id or without an org is never matched or recorded', async () => {
    await deliver({ orgId: 'org_a', type: 't' })
    await deliver({ orgId: 'org_a', type: 't' })
    await deliver({ type: 't', eventId: 'evt-3' })
    await deliver({ type: 't', eventId: 'evt-3' })
    expect(runs).to.have.length(4)
    expect(inbox.calls).to.deep.equal([])
  })

  it('a consumer that names no group does not use the inbox', async () => {
    const m = signed()
    await deliver(m, '')
    await deliver(m, '')
    expect(runs).to.have.length(2)
    expect(inbox.calls).to.deep.equal([])
  })

  it('an inbox that cannot be read lets the message run; one that cannot be written does not fail it', async () => {
    inbox.failSeen = true
    expect(await deliver(signed())).to.equal('done')
    inbox.failSeen = false
    inbox.failRecord = true
    const m = signed()
    expect(await deliver(m)).to.equal('done')
    // nothing was recorded, so a redelivery runs again (at-least-once, as without an inbox)
    expect(await deliver(m)).to.equal('done')
    expect(runs).to.have.length(3)
  })

  it('a handler recording in its own unit is not recorded again, and a concurrent copy it refuses is acknowledged', async () => {
    const m = signed()
    const inUnit = async (v: unknown) => withDeliveryRecorded(async () => { runs.push(v); return 'stored' })
    expect(await deliver(m, G, inUnit)).to.equal('stored')
    expect(inbox.calls).to.deep.equal(['seen', 'recordWith'])
    // a copy that passed `seen` before the first committed: recordWith refuses it and nothing is stored twice
    inbox.calls = []
    const racing = new MemInbox()
    configureInbox(racing)
    racing.seen = async () => false
    racing.rows = inbox.rows
    expect(await deliver(m, G, inUnit)).to.equal(undefined)
    expect(runs).to.have.length(1)
  })

  it('a handler recording in its own unit that fails records nothing, so its retry runs and records', async () => {
    const m = signed()
    let fail = true
    const inUnit = async (v: unknown) => withDeliveryRecorded(async () => { runs.push(v); if (fail) throw new Error('store away'); return 'stored' })
    let thrown: unknown
    try { await deliver(m, G, inUnit) } catch (e) { thrown = e }
    expect((thrown as Error).message).to.equal('store away')
    expect(inbox.rows.size).to.equal(0)
    fail = false
    expect(await deliver(m, G, inUnit)).to.equal('stored')
    expect(await deliver(m, G, inUnit)).to.equal(undefined)
    expect(runs).to.have.length(2)
  })

  it('a message with no org of its own is recorded per org it fans out to, never as a whole', async () => {
    const m = { audience: 'platformAdmins', type: 't', eventId: 'evt-fan' }
    const fanOut = async () => {
      let done = 0
      for (const org of ['org_a', 'org_b']) {
        try { await withDeliveryRecorded(async () => { runs.push(org) }, org); done += 1 } catch (e) { if (!(e instanceof DuplicateEnvelope)) throw e }
      }
      return done
    }
    expect(await deliver(m, G, fanOut)).to.equal(2)
    expect(await deliver(m, G, fanOut)).to.equal(0)
    expect(runs).to.deep.equal(['org_a', 'org_b'])
    expect(inbox.calls).to.deep.equal(['recordWith', 'recordWith', 'recordWith', 'recordWith'])
  })

  it('a step per org is done only once recorded; an inbox that cannot answer says not done; outside a delivery it is never done', async () => {
    const m = { audience: 'platformAdmins', type: 't', eventId: 'evt-step' }
    const step = async () => {
      const before = await deliveryStepDone('mail', 'org_a')
      await recordDeliveryStep('mail', 'org_a')
      return before
    }
    expect(await deliver(m, G, step)).to.equal(false)
    expect(await deliver(m, G, step)).to.equal(true)
    expect(await deliver({ ...m, eventId: 'evt-other' }, G, async () => deliveryStepDone('mail', 'org_a'))).to.equal(false)
    inbox.failSeen = true
    expect(await deliver(m, G, async () => deliveryStepDone('mail', 'org_a'))).to.equal(false)
    expect(await deliveryStepDone('mail', 'org_a')).to.equal(false)
  })

  it('outside a delivery, withDeliveryRecorded just runs its function', async () => {
    expect(await withDeliveryRecorded(async () => 7)).to.equal(7)
    expect(inbox.calls).to.deep.equal([])
  })

  it('keeps ids for the same window as the Python consumers (age, admission, skew)', () => {
    const prev = [process.env.ENVELOPE_MAX_AGE_SECONDS, process.env.ENVELOPE_ADMITTED_MAX_AGE_SECONDS]
    try {
      delete process.env.ENVELOPE_MAX_AGE_SECONDS
      delete process.env.ENVELOPE_ADMITTED_MAX_AGE_SECONDS
      expect(replayWindowSeconds()).to.equal(86_700)
      process.env.ENVELOPE_MAX_AGE_SECONDS = '100000'
      expect(replayWindowSeconds()).to.equal(100_300)
      process.env.ENVELOPE_ADMITTED_MAX_AGE_SECONDS = '0'
      expect(() => replayWindowSeconds()).to.throw(/ENVELOPE_ADMITTED_MAX_AGE_SECONDS must be a positive number/)
    } finally {
      for (const [k, v] of [['ENVELOPE_MAX_AGE_SECONDS', prev[0]], ['ENVELOPE_ADMITTED_MAX_AGE_SECONDS', prev[1]]] as const) {
        if (v === undefined) delete process.env[k]
        else process.env[k] = v
      }
    }
  })
})
