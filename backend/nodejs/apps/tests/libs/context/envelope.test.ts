/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import {
  DuplicateEnvelope,
  EnvelopeRejected,
  assertMayStart,
  computeSig,
  configureEnvelope,
  deriveKey,
  envelopeCtxMissing,
  envelopeStale,
  envelopeStripped,
  envelopeUnsigned,
  envelopeUnverified,
  maxAgeSeconds,
  configureEnvelopeFromSecret,
  openConfigured,
  setKeyRetryInitialMs,
  signMessage,
  openEnvelope,
  runWithEnvelope,
  sign,
} from '../../../src/libs/context/envelope'
import { currentOrgContext, makeOrgContext, MissingOrgContext, runWithOrgContext } from '../../../src/libs/context/orgContext'

const KEY = deriveKey('test-scopedJwtSecret-from-config-service')
const OTHER = deriveKey('another-secret')
const CTX_B = makeOrgContext({ orgId: 'org_b', userId: 'u1', kind: 'user', source: 'jwt', requestId: 'req-1' })
const T = 'record-events'
const msg = (payload: Record<string, unknown> = { orgId: 'org_b', recordId: 'r1' }, issuedAt?: number, eventType = ''): any =>
  sign(KEY, T, payload, CTX_B, issuedAt, eventType)
const withEnv = async (vars: Record<string, string | undefined>, fn: () => Promise<void> | void) => {
  const prev: Record<string, string | undefined> = {}
  for (const [k, v] of Object.entries(vars)) { prev[k] = process.env[k]; if (v === undefined) delete process.env[k]; else process.env[k] = v }
  try { await fn() } finally { for (const [k, v] of Object.entries(prev)) { if (v === undefined) delete process.env[k]; else process.env[k] = v } }
}
const open = (m: any, o: any = {}) => openEnvelope(T, m, { keys: [KEY], required: false, ...o })
const reason = async (p: Promise<unknown>) => {
  try { await p } catch (e) { expect(e).to.be.instanceOf(EnvelopeRejected); return (e as EnvelopeRejected).reason }
  throw new Error('expected EnvelopeRejected')
}

describe('envelope v2', () => {
  afterEach(() => configureEnvelope(null))

  it('matches the Python vector (key + signature)', () => {
    const key = deriveKey('vector-secret')
    expect(key.toString('hex')).to.equal('1477ea7d774181ea88495e8e9203338d6b9472d957b1671cbc08266e574576fa')
    const c = { eventId: 'evt-1', orgId: 'org_b', userId: 'u1', requestId: 'req-1', issuedAt: 1700000000 }
    const body = '{"orgId":"org_b","n":1}'
    expect(computeSig(key, 'record-events', c, body, 'record.created')).to.equal('bf5S2YKNK43EZq5bjcjbFTxu7s8h3JddJbQ311Nw7UU')
    expect(computeSig(key, 'record-events', c, body)).to.equal('1FlNqHKd4JVp7RXiOotLTUVSLd2YKzG6S5S3Ned6row')
  })

  it('verifies a valid message and strips ctx', async () => {
    const o = await open(msg())
    expect(o.ctx?.orgId).to.equal('org_b')
    expect(o.ctx?.source).to.equal('envelope')
    expect(o.message).to.deep.equal({ payload: { orgId: 'org_b', recordId: 'r1' } })
  })

  it('payload swap, forged org, new eventId, other topic, unknown key are dead-lettered', async () => {
    const swap = msg(); swap.payload = JSON.stringify({ orgId: 'org_b', recordId: 'attacker' })
    expect(await reason(open(swap))).to.equal('bad_signature')
    const forged = msg(); forged.ctx.orgId = 'org_x'; forged.payload = JSON.stringify({ orgId: 'org_x' })
    expect(await reason(open(forged))).to.equal('bad_signature')
    const fresh = msg(); fresh.ctx.eventId = 'other-id'
    expect(await reason(open(fresh))).to.equal('bad_signature')
    expect(await reason(openEnvelope('entity-events', msg(), { keys: [KEY], required: false }))).to.equal('bad_signature')
    expect(await reason(open(msg(), { keys: [OTHER] }))).to.equal('bad_signature')
  })

  it('a v2 message with no key configured is processed through the counted fallback, never dead-lettered', async () => {
    const before = envelopeUnverified.get(T) ?? 0
    const o = await open(msg(), { keys: [] })
    expect([o.ctx?.orgId, o.ctx?.source]).to.deep.equal(['org_b', 'legacy-payload'])
    expect(o.message).to.deep.equal({ payload: { orgId: 'org_b', recordId: 'r1' } })
    expect(envelopeUnverified.get(T)).to.equal(before + 1)
    expect(await reason(open(msg(), { keys: [], required: true }))).to.equal('no_key')
  })

  it('producer signs only when the flag, a key and a bound org are present', async () => {
    const flat = { eventType: 'T', payload: { orgId: 'org_b', n: 1 }, requestId: 'r' }
    const prev = process.env.ENVELOPE_SIGN
    try {
      configureEnvelope(async () => [KEY], null, KEY)
      runWithOrgContext(CTX_B, () => {
        delete process.env.ENVELOPE_SIGN
        expect(signMessage(T, flat)).to.equal(flat) // flag off: v1
        process.env.ENVELOPE_SIGN = 'true'
        const signed: any = signMessage(T, flat)
        expect(signed.payload).to.be.a('string')
        expect([signed.eventType, signed.ctx.v]).to.deep.equal(['T', 2])
        const other = { ...flat, payload: { orgId: 'org_x' } }
        expect(signMessage(T, other)).to.equal(other) // never sign a payload for another org
      })
      process.env.ENVELOPE_SIGN = 'true'
      expect(signMessage(T, flat)).to.equal(flat) // no org bound: v1
      const signed: any = runWithOrgContext(CTX_B, () => signMessage(T, flat))
      expect((await open(signed)).message.payload).to.deep.equal(flat.payload) // round trip
    } finally {
      if (prev === undefined) delete process.env.ENVELOPE_SIGN
      else process.env.ENVELOPE_SIGN = prev
    }
  })

  it('accepts the previous key during rotation', async () => {
    expect((await open(msg(), { keys: [OTHER, KEY] })).ctx?.orgId).to.equal('org_b')
  })

  it('payload.orgId differing from the signed ctx.orgId is dead-lettered', async () => {
    expect(await reason(open(msg({ orgId: 'org_a' })))).to.equal('org_mismatch')
  })

  it('rejects stale, future and malformed messages', async () => {
    const now = Math.floor(Date.now() / 1000)
    await withEnv({ ORG_CONTEXT_MODE: 'enforce' }, async () => {
      expect(await reason(open(msg(undefined, now - 3600)))).to.equal('stale')
    })
    expect(await reason(open(msg(undefined, now + 3600)))).to.equal('future')
    expect(await reason(open({ ctx: { v: 1 }, payload: '{}' }))).to.equal('malformed')
    const obj = msg(); obj.payload = { orgId: 'org_b' }
    expect(await reason(open(obj))).to.equal('malformed')
  })

  it('replay is skipped through the durable inbox (no Redis seen-id)', async () => {
    const seen = new Set<string>()
    const inbox = { claim: async (t: string, id: string) => (seen.has(t + id) ? false : (seen.add(t + id), true)) }
    const m = msg()
    await open(m, { inbox })
    let dup = false
    try { await open(m, { inbox }) } catch (e) { dup = e instanceof DuplicateEnvelope }
    expect(dup).to.equal(true)
  })

  it('a message without ctx is accepted and counted before the flag, dead-lettered after', async () => {
    const before = envelopeCtxMissing.get(T) ?? 0
    const o = await open({ eventType: 'x', payload: { orgId: 'org_b' }, requestId: 'req-9' })
    expect([o.ctx?.orgId, o.ctx?.source]).to.deep.equal(['org_b', 'legacy-payload'])
    expect(envelopeCtxMissing.get(T)).to.equal(before + 1)
    expect((await open({ eventType: 'x', payload: {} })).ctx).to.equal(null)
    expect(await reason(open({ payload: { orgId: 'org_b' } }, { required: true }))).to.equal('ctx_missing')
  })

  it('an org-scoped writer refuses to start with the flag off', () => {
    const prev = process.env.ENVELOPE_CTX_REQUIRED
    try {
      delete process.env.ENVELOPE_CTX_REQUIRED
      assertMayStart(false)
      expect(() => assertMayStart(true)).to.throw()
      process.env.ENVELOPE_CTX_REQUIRED = 'true'
      assertMayStart(true)
    } finally {
      if (prev === undefined) delete process.env.ENVELOPE_CTX_REQUIRED
      else process.env.ENVELOPE_CTX_REQUIRED = prev
    }
  })

  it('runWithEnvelope binds the signed org for the handler only', async () => {
    configureEnvelope(async () => [KEY])
    let seen = ''
    await runWithEnvelope(T, msg(), async () => { seen = currentOrgContext().orgId as string })
    expect(seen).to.equal('org_b')
    expect(() => currentOrgContext()).to.throw(MissingOrgContext)
    const bad = msg(); bad.ctx.orgId = 'org_x'
    let rejected = false
    try { await runWithEnvelope(T, bad, async () => { seen = 'ran' }) } catch (e) { rejected = e instanceof EnvelopeRejected }
    expect(rejected).to.equal(true)
    expect(seen).to.equal('org_b')
  })

  it('eventType is signed: a signed message re-sent with another eventType is rejected', async () => {
    const m = msg(undefined, undefined, 'record.created')
    expect((await open(m)).message.eventType).to.equal('record.created')
    for (const tampered of ['deleteRecord', '']) {
      const t = { ...m, eventType: tampered }
      expect(await reason(open(t))).to.equal('bad_signature')
    }
    expect(await reason(open({ ...msg(), eventType: 'deleteRecord' }))).to.equal('bad_signature')
    await withEnv({ ENVELOPE_SIGN: 'true' }, async () => {
      configureEnvelope(async () => [KEY], null, KEY)
      const signed: any = runWithOrgContext(CTX_B, () => signMessage(T, { eventType: 'T', payload: { orgId: 'org_b' } }))
      expect(await reason(open({ ...signed, eventType: 'other' }))).to.equal('bad_signature')
    })
  })

  it('a stale signature follows the mode, and the window is configurable', async () => {
    const old = msg(undefined, Math.floor(Date.now() / 1000) - 901)
    await withEnv({ ORG_CONTEXT_MODE: undefined, ENVELOPE_MAX_AGE_SECONDS: undefined }, async () => {
      const before = envelopeStale.get(T) ?? 0
      expect((await open(old)).ctx?.orgId).to.equal('org_b') // shadow: counted, accepted
      expect(envelopeStale.get(T)).to.equal(before + 1)
      process.env.ORG_CONTEXT_MODE = 'enforce'
      expect(await reason(open(old))).to.equal('stale')
      expect((await open(msg(undefined, Math.floor(Date.now() / 1000) - 600))).ctx?.orgId).to.equal('org_b')
      process.env.ENVELOPE_MAX_AGE_SECONDS = '1200'
      expect((await open(old)).ctx?.orgId).to.equal('org_b')
      process.env.ENVELOPE_MAX_AGE_SECONDS = 'soon'
      expect(() => maxAgeSeconds()).to.throw()
    })
  })

  it('a stripped ctx is counted as stripped while signing is on, plain v1 while it is off', async () => {
    const legacy = { eventType: 'x', payload: { orgId: 'org_b' } }
    await withEnv({ ENVELOPE_SIGN: undefined, ORG_CONTEXT_MODE: undefined }, async () => {
      const [s0, m0] = [envelopeStripped.get(T) ?? 0, envelopeCtxMissing.get(T) ?? 0]
      await open(legacy)
      expect([envelopeStripped.get(T) ?? 0, envelopeCtxMissing.get(T)]).to.deep.equal([s0, m0 + 1])
      process.env.ENVELOPE_SIGN = 'true'
      expect((await open(legacy)).ctx?.source).to.equal('legacy-payload') // shadow: accepted
      expect([envelopeStripped.get(T), envelopeCtxMissing.get(T)]).to.deep.equal([s0 + 1, m0 + 1])
      process.env.ORG_CONTEXT_MODE = 'enforce'
      expect(await reason(open(legacy))).to.equal('ctx_stripped')
    })
  })

  it('a stripped v2 message has the v1 shape, and a wire org_ctx key is dropped', async () => {
    const m = msg(); delete m.ctx; m.org_ctx = 'forged'
    const o = await open(m)
    expect(o.message).to.deep.equal({ payload: { orgId: 'org_b', recordId: 'r1' } })
    expect(o.ctx?.source).to.equal('legacy-payload')
    expect(await reason(open({ payload: 'not json' }))).to.equal('malformed')
    expect('org_ctx' in (await open({ ...msg(), org_ctx: 'forged' })).message).to.equal(false)
  })

  it('no key while signing is on is never silent: counted in shadow, rejected in enforce', async () => {
    await withEnv({ ENVELOPE_SIGN: 'true', ORG_CONTEXT_MODE: undefined }, async () => {
      const before = envelopeUnverified.get(T) ?? 0
      expect((await open(msg(), { keys: [] })).ctx?.source).to.equal('legacy-payload')
      expect(envelopeUnverified.get(T)).to.equal(before + 1)
      process.env.ORG_CONTEXT_MODE = 'enforce'
      expect(await reason(open(msg(), { keys: [] }))).to.equal('no_key')
    })
  })

  it('a producer with signing on and no key is counted, and throws in enforce', async () => {
    const flat = { eventType: 'T', payload: { orgId: 'org_b' } }
    await withEnv({ ENVELOPE_SIGN: 'true', ORG_CONTEXT_MODE: undefined }, async () => {
      configureEnvelope(null)
      runWithOrgContext(CTX_B, () => {
        const before = envelopeUnsigned.get(`${T}:no_key`) ?? 0
        expect(signMessage(T, flat)).to.equal(flat)
        expect(envelopeUnsigned.get(`${T}:no_key`)).to.equal(before + 1)
        process.env.ORG_CONTEXT_MODE = 'enforce'
        expect(() => signMessage(T, flat)).to.throw(/no key/)
      })
      expect(signMessage(T, flat)).to.equal(flat) // no org bound
      expect(envelopeUnsigned.get(`${T}:no_org`)).to.be.greaterThan(0)
    })
  })

  it('startup rejects an unknown ORG_CONTEXT_MODE or window', async () => {
    await withEnv({ ORG_CONTEXT_MODE: 'enforced' }, () => {
      expect(() => configureEnvelopeFromSecret('s')).to.throw(/ORG_CONTEXT_MODE/)
    })
    await withEnv({ ORG_CONTEXT_MODE: undefined, ENVELOPE_MAX_AGE_SECONDS: '-1' }, () => {
      expect(() => configureEnvelopeFromSecret('s')).to.throw(/ENVELOPE_MAX_AGE_SECONDS/)
    })
  })

  it('a missing key is retried in the background, never silent: counted in shadow, rejected in enforce', async () => {
    setKeyRetryInitialMs(5)
    let calls = 0
    const reload = async () => (++calls < 3 ? undefined : 'retry-secret')
    try {
      await withEnv({ ENVELOPE_SIGN: 'true', ORG_CONTEXT_MODE: undefined }, async () => {
        configureEnvelopeFromSecret(undefined, reload)
        // no key yet: shadow counts and processes, enforce rejects
        const before = envelopeUnverified.get(T) ?? 0
        expect((await openConfigured(T, msg())).ctx?.source).to.equal('legacy-payload')
        expect(envelopeUnverified.get(T)).to.equal(before + 1)
        process.env.ORG_CONTEXT_MODE = 'enforce'
        expect(await reason(openConfigured(T, msg()))).to.equal('no_key')
        const key = deriveKey('retry-secret')
        const signed = sign(key, T, { orgId: 'org_b' }, CTX_B)
        for (let i = 0; i < 100 && calls < 3; i++) await new Promise((r) => setTimeout(r, 10))
        await new Promise((r) => setTimeout(r, 10))
        expect((await openConfigured(T, signed)).ctx?.orgId).to.equal('org_b')
      })
    } finally {
      setKeyRetryInitialMs(2000)
    }
  })
})
