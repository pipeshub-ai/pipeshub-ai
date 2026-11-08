import { expect } from 'chai'
import sinon from 'sinon'
import type { DocumentRepository } from '../../../../src/libs/repositories/document'
import { NotFoundError } from '../../../../src/libs/errors/http.errors'
import { END_BUDGET_MS, withWriteWindow, writeWindowHooks, writeWindowMs } from '../../../../src/modules/storage/controllers/storage.write-window'

const doc = { _id: 'd1', orgId: 'o1' }
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

function fakeRepo(over: Partial<Record<keyof DocumentRepository, (...a: any[]) => Promise<any>>> = {}) {
  const calls: string[] = []
  const rec = (name: string, fn: (...a: any[]) => Promise<any>) => async (...a: any[]) => {
    calls.push(name)
    return fn(...a)
  }
  const repo = {
    beginWrite: rec('begin', over.beginWrite ?? (async () => ({ writeSeq: 1 }))),
    renewWrite: rec('renew', over.renewWrite ?? (async () => undefined)),
    endWrite: rec('end', over.endWrite ?? (async () => undefined)),
  } as unknown as DocumentRepository
  return { repo, calls }
}

describe('withWriteWindow', () => {
  const saved = process.env.STORAGE_WRITE_WINDOW_MS
  afterEach(() => {
    sinon.restore()
    writeWindowHooks.repo = undefined
    writeWindowHooks.heartbeat = 'auto'
    writeWindowHooks.onBegin = undefined
    if (saved === undefined) delete process.env.STORAGE_WRITE_WINDOW_MS
    else process.env.STORAGE_WRITE_WINDOW_MS = saved
  })

  it('U1: begin, then the write, then end', async () => {
    const { repo, calls } = fakeRepo()
    writeWindowHooks.repo = () => repo
    const out = await withWriteWindow(doc, async () => {
      calls.push('write')
      return 7
    })
    expect(out).to.equal(7)
    expect(calls).to.deep.equal(['begin', 'write', 'end'])
  })

  it('U2: end runs when the write throws, and the original error survives an end that also fails', async () => {
    const { repo, calls } = fakeRepo({ endWrite: async () => { throw new Error('end down') } })
    writeWindowHooks.repo = () => repo
    let err: unknown
    try { await withWriteWindow(doc, async () => { throw new Error('write broke') }) } catch (e) { err = e }
    expect((err as Error).message).to.equal('write broke')
    expect(calls.filter((c) => c === 'end')).to.have.length(2) // two attempts
  })

  it('U3: a row that is gone fails before any byte', async () => {
    const { repo, calls } = fakeRepo({ beginWrite: async () => null })
    writeWindowHooks.repo = () => repo
    let wrote = false
    let err: unknown
    try { await withWriteWindow(doc, async () => { wrote = true }) } catch (e) { err = e }
    expect(err).to.be.instanceOf(NotFoundError)
    expect(wrote).to.equal(false)
    expect(calls).to.deep.equal(['begin'])
  })

  it('U4: a begin that fails (busy, clock down) writes nothing and ends nothing', async () => {
    const { repo, calls } = fakeRepo({ beginWrite: async () => { throw new Error('busy') } })
    writeWindowHooks.repo = () => repo
    let wrote = false
    try { await withWriteWindow(doc, async () => { wrote = true }) } catch { /* expected */ }
    expect(wrote).to.equal(false)
    expect(calls).to.deep.equal(['begin'])
  })

  it('U5: an end that stalls does not hold a successful response past the budget, and does not change the outcome', async () => {
    const { repo } = fakeRepo({ endWrite: () => new Promise(() => undefined) })
    writeWindowHooks.repo = () => repo
    const t0 = Date.now()
    const out = await withWriteWindow(doc, async () => 'ok')
    expect(out).to.equal('ok')
    expect(Date.now() - t0).to.be.within(END_BUDGET_MS - 100, END_BUDGET_MS + 1500)
  }).timeout(8000)

  it('U6: an end whose first attempt fails is retried once and the write still succeeds', async () => {
    let n = 0
    const { repo, calls } = fakeRepo({ endWrite: async () => { if (++n === 1) throw new Error('blip') } })
    writeWindowHooks.repo = () => repo
    expect(await withWriteWindow(doc, async () => 'ok')).to.equal('ok')
    expect(calls.filter((c) => c === 'end')).to.have.length(2)
  })

  it('U7: the heartbeat renews the token while the write runs and stops after it; a failing renew never fails the write', async () => {
    process.env.STORAGE_WRITE_WINDOW_MS = '90'
    let renews = 0
    const { repo } = fakeRepo({ renewWrite: async () => { renews++; throw new Error('db blip') } })
    writeWindowHooks.repo = () => repo
    expect(await withWriteWindow(doc, async () => { await wait(200); return 'ok' })).to.equal('ok')
    const during = renews
    expect(during).to.be.greaterThan(2)
    await wait(120)
    expect(renews).to.equal(during)
  })

  it('manual heartbeat mode never renews by itself and announces the token', async () => {
    process.env.STORAGE_WRITE_WINDOW_MS = '60'
    writeWindowHooks.heartbeat = 'manual'
    let seen: string | undefined
    writeWindowHooks.onBegin = (i) => { seen = i.token }
    const { repo, calls } = fakeRepo()
    writeWindowHooks.repo = () => repo
    await withWriteWindow(doc, async () => { await wait(150) })
    expect(calls).to.not.include('renew')
    expect(seen).to.match(/^[0-9a-f]{32}$/)
  })

  it('the window defaults to five minutes and reads STORAGE_WRITE_WINDOW_MS', () => {
    delete process.env.STORAGE_WRITE_WINDOW_MS
    expect(writeWindowMs()).to.equal(300000)
    process.env.STORAGE_WRITE_WINDOW_MS = '1500'
    expect(writeWindowMs()).to.equal(1500)
    process.env.STORAGE_WRITE_WINDOW_MS = 'soon'
    expect(writeWindowMs()).to.equal(300000)
  })
})
