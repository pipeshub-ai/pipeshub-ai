import { expect } from 'chai'
import { UniqueConstraintError } from '../../../src/libs/errors/database.errors'
import { InternalServerError } from '../../../src/libs/errors/http.errors'
import type { PgKernelTx } from '../../../src/libs/repositories/agent-harness/pg-documents/pg'
import {
  ONE_ACTIVE_RUN,
  activeSessionKey,
  after,
  appendRunEvent,
  bumpSessionCounter,
  insertRun,
  insertRunEvents,
  isUniqueViolation,
  lockRun,
  notAfter,
  placedRun,
  readRun,
  readRuns,
  readSessionCounter,
  runDocs,
  saveRun,
  setCompactRequest,
} from '../../../src/libs/repositories/agent-harness/execution/pg-rows'

/**
 * The PostgreSQL execution store's shared row helpers, against a kernel unit that records each statement and answers
 * what the test queues: the row mapping, the defaults an insert fills in, the guards on a write's row count and the
 * counter decoding. The statements' own semantics (locks, upserts, ordering) are the PostgreSQL itests' (pg-stores).
 */
interface Call {
  text: string
  values: unknown[]
}

function kernel(answers: Array<{ rows?: unknown[]; rowCount?: number }>): { k: PgKernelTx; calls: Call[] } {
  const calls: Call[] = []
  const k = {
    query: async (text: string, values: unknown[] = []) => {
      calls.push({ text, values })
      const next = answers.shift() ?? {}
      return { rows: next.rows ?? [], rowCount: next.rowCount ?? (next.rows ?? []).length }
    },
  } as unknown as PgKernelTx
  return { k, calls }
}

const tag = (text: string): string => /\/\* pipeshub:([\w.]+) \*\//.exec(text)?.[1] ?? ''

const runDoc = (extra: Record<string, unknown> = {}) => ({
  orgId: 'o1',
  runId: 'r1',
  sessionId: 's1',
  status: 'queued',
  revision: 3,
  createdAt: 1,
  updatedAt: 2,
  ...extra,
})

describe('execution pg-rows: the run rows every PostgreSQL execution store shares', () => {
  it('keys the active session by org and session, and places a run in the region its row was written in', () => {
    expect(activeSessionKey('o1', 's1')).to.equal('["o1","s1"]')
    const run = placedRun({ doc: runDoc(), region: 'eu' })
    expect(run.region).to.equal('eu')
    expect(run.runId).to.equal('r1')
  })

  it('locks and reads one run of the org, null when the org has none', async () => {
    const { k, calls } = kernel([{ rows: [{ doc: runDoc(), region: 'us' }] }, { rows: [] }, { rows: [{ doc: runDoc(), region: 'eu' }] }])
    expect((await lockRun(k, 'o1', 'r1'))?.region).to.equal('us')
    expect(await lockRun(k, 'o1', 'r2')).to.equal(null)
    expect((await readRun(k, 'o1', 'r1'))?.doc['runId']).to.equal('r1')
    expect(calls.map((c) => tag(c.text))).to.deep.equal(['harness.runs_lock', 'harness.runs_lock', 'harness.runs_read'])
    expect(calls[0]!.text).to.contain('FOR UPDATE')
    expect(calls[2]!.text).not.to.contain('FOR UPDATE')
    expect(calls[1]!.values).to.deep.equal(['o1', 'r2'])
  })

  it('reads several runs in one statement, and none without asking when no id is given', async () => {
    const { k, calls } = kernel([{ rows: [{ doc: runDoc() }, { doc: runDoc({ runId: 'r2' }) }] }])
    expect(await readRuns(k, 'o1', [])).to.deep.equal([])
    expect(calls).to.have.length(0)
    const docs = await readRuns(k, 'o1', ['r1', 'r2'])
    expect(docs.map((d) => d['runId'])).to.deep.equal(['r1', 'r2'])
    expect(calls[0]!.values).to.deep.equal(['o1', ['r1', 'r2']])
    const { k: k2 } = kernel([{ rows: [{ doc: runDoc({ runId: 'r9' }) }] }])
    expect((await runDocs(k2, 'SELECT doc', [1]))[0]!['runId']).to.equal('r9')
  })

  it('writes a locked run back, and a write that found no row is a fault, never a silent no-op', async () => {
    const { k, calls } = kernel([{ rowCount: 1 }, { rowCount: 0 }])
    await saveRun(k, runDoc())
    expect(calls[0]!.values.slice(0, 2)).to.deep.equal(['o1', 'r1'])
    expect(JSON.parse(String(calls[0]!.values[2]))).to.include({ runId: 'r1', status: 'queued' })
    try {
      await saveRun(k, runDoc())
      expect.fail('a lost row must not save')
    } catch (error) {
      expect(error).to.be.instanceOf(InternalServerError)
    }
  })

  it("inserts a run with the Mongo run schema's defaults for every field the caller left out", async () => {
    const { k, calls } = kernel([{ rowCount: 1 }])
    await insertRun(k, runDoc({ attempts: 2 }))
    const stored = JSON.parse(String(calls[0]!.values[2])) as Record<string, unknown>
    expect(stored).to.include({ attempts: 2, harnessMajor: 1, via: 'session' })
    expect(stored['outcome']).to.equal(null)
    expect(stored['pinned']).to.equal(null)
    expect(calls[0]!.values[3]).to.be.a('string')
  })

  it('classes a unique violation by the constraint it broke', () => {
    const active = new UniqueConstraintError('dup', { constraint: ONE_ACTIVE_RUN })
    expect(isUniqueViolation(active)).to.equal(true)
    expect(isUniqueViolation(new UniqueConstraintError('dup', { constraint: 'runs_pkey' }))).to.equal(true)
    expect(isUniqueViolation(new UniqueConstraintError('dup', { constraint: 'other' }))).to.equal(false)
    expect(isUniqueViolation(new UniqueConstraintError('dup'))).to.equal(false)
    expect(isUniqueViolation(active, ['runs_pkey'])).to.equal(false)
    expect(isUniqueViolation(new Error('dup'))).to.equal(false)
  })

  it("bumps the session counter with each name once, and decodes the counter's row", async () => {
    const row = { next_event_seq: '7', journal_features: ['a'], actors: ['u1'], compact_request: null }
    const { k, calls } = kernel([{ rows: [row] }, { rows: [] }])
    const counter = await bumpSessionCounter(k, 'o1', 's1', { events: 2, features: ['a', 'a', 'b'], actors: ['u1', 'u1'] })
    expect(counter).to.deep.equal({ nextEventSeq: 7, journalFeatures: ['a'], actors: ['u1'], compactRequest: null })
    expect(calls[0]!.values.slice(0, 5)).to.deep.equal(['o1', 's1', 2, ['a', 'b'], ['u1']])
    try {
      await bumpSessionCounter(k, 'o1', 's1', {})
      expect.fail('an upsert that answers nothing is a fault')
    } catch (error) {
      expect(error).to.be.instanceOf(InternalServerError)
    }
  })

  it('reads the counter locked or not, with a waiting /compact decoded, and null for a session without one', async () => {
    const compact = { requestedBy: 'u1', at: 5 }
    const { k, calls } = kernel([
      { rows: [{ next_event_seq: '3', journal_features: [], actors: [], compact_request: compact }] },
      { rows: [] },
    ])
    expect((await readSessionCounter(k, 'o1', 's1', true))?.compactRequest).to.deep.equal(compact)
    expect(await readSessionCounter(k, 'o1', 's2')).to.equal(null)
    expect(tag(calls[0]!.text)).to.equal('harness.session_counters_lock')
    expect(tag(calls[1]!.text)).to.equal('harness.session_counters_read')
  })

  it('sets or clears the waiting /compact, false where no run was admitted', async () => {
    const { k, calls } = kernel([{ rowCount: 1 }, { rowCount: 0 }])
    expect(await setCompactRequest(k, 'o1', 's1', { by: 'u1' })).to.equal(true)
    expect(await setCompactRequest(k, 'o1', 's2', null)).to.equal(false)
    expect(JSON.parse(String(calls[0]!.values[2]))).to.deep.equal({ by: 'u1' })
    expect(calls[1]!.values[2]).to.equal(null)
  })

  it("appends one event at the seq the counter allocated, in the run's commit after its revision", async () => {
    const { k, calls } = kernel([
      { rows: [{ next_event_seq: '11', journal_features: [], actors: [], compact_request: null }] },
      { rowCount: 1 },
    ])
    const seq = await appendRunEvent(k, { orgId: 'o1', sessionId: 's1', runId: 'r1', revision: 4 }, 'run.note', { a: 1 }, 99)
    expect(seq).to.equal(11)
    const insert = calls[1]!
    expect(tag(insert.text)).to.equal('harness.run_events_insert')
    expect(insert.values.slice(0, 3)).to.deep.equal(['o1', 's1', 'r1'])
    expect(insert.values[4]).to.deep.equal([11])
    expect(insert.values[5]).to.deep.equal([5])
    expect(JSON.parse((insert.values[6] as string[])[0]!)).to.include({ type: 'run.note', createdAt: 99 })
    const { k: none, calls: noCalls } = kernel([])
    await insertRunEvents(none, [])
    expect(noCalls).to.have.length(0)
  })

  it("compares a deadline as Mongo's $isNumber guards do: only a number is after or not after now", () => {
    expect(after(5, 4)).to.equal(true)
    expect(after(4, 4)).to.equal(false)
    expect(after('5', 4)).to.equal(false)
    expect(notAfter(4, 4)).to.equal(true)
    expect(notAfter(5, 4)).to.equal(false)
    expect(notAfter(null, 4)).to.equal(false)
  })
})
