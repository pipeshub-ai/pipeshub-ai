/**
 * The heartbeat of a writer window must stop whichever way the write ends (success, a throw, a throw with a failing end, a renew still
 * in flight) and leave no timer behind. Added by independent QA of STOR-FP-b: the existing U7 covers the success path only.
 */
import { expect } from 'chai'
import {
  withWriteWindow,
  writeWindowHooks,
} from '../../../../src/modules/storage/controllers/storage.write-window'

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))
const timers = (): number =>
  process.getActiveResourcesInfo().filter((t) => t === 'Timeout').length
const doc = { _id: '0123456789abcdef01234567', orgId: '0123456789abcdef01234567' }

function fakeRepo(opts: { endFails?: boolean; renewMs?: number } = {}) {
  const calls = { renew: 0, end: 0 }
  const repo = {
    beginWrite: async () => ({ writeSeq: 1 }),
    renewWrite: async () => {
      calls.renew++
      if (opts.renewMs) await wait(opts.renewMs)
    },
    endWrite: async () => {
      calls.end++
      if (opts.endFails) throw new Error('db down')
    },
  }
  return { repo, calls }
}

describe('writer window heartbeat lifecycle (QA)', () => {
  const saved = {
    window: process.env.STORAGE_WRITE_WINDOW_MS,
    mode: writeWindowHooks.heartbeat,
    repo: writeWindowHooks.repo,
  }
  before(() => {
    process.env.STORAGE_WRITE_WINDOW_MS = '30'
    writeWindowHooks.heartbeat = 'auto'
  })
  after(() => {
    if (saved.window === undefined) delete process.env.STORAGE_WRITE_WINDOW_MS
    else process.env.STORAGE_WRITE_WINDOW_MS = saved.window
    writeWindowHooks.heartbeat = saved.mode
    writeWindowHooks.repo = saved.repo
  })

  const cases: [string, { endFails?: boolean; renewMs?: number }, () => Promise<void>][] = [
    ['the write succeeds', {}, async () => { await wait(80) }],
    ['the write throws', {}, async () => { await wait(40); throw new Error('boom') }],
    ['the write throws and the end fails too', { endFails: true }, async () => { await wait(40); throw new Error('boom') }],
    ['a renew is still in flight when the write ends', { renewMs: 60 }, async () => { await wait(40) }],
    ['the write throws at once', {}, async () => { throw new Error('boom') }],
  ]
  for (const [name, opts, work] of cases) {
    it(`${name}: the heartbeat stops and no timer is left`, async () => {
      await wait(30)
      const base = timers()
      const { repo, calls } = fakeRepo(opts)
      writeWindowHooks.repo = () => repo as never
      await withWriteWindow(doc, work).catch(() => undefined)
      const atEnd = calls.renew
      await wait(200)
      expect(calls.renew, 'a renew started after the window ended').to.be.at.most(atEnd + 1)
      expect(calls.end).to.be.greaterThan(0)
      expect(timers(), 'timers left behind').to.be.at.most(base)
    })
  }

  it('200 overlapping windows, a third of them failing, leave no timer', async () => {
    await wait(100)
    const base = timers()
    const { repo } = fakeRepo()
    writeWindowHooks.repo = () => repo as never
    await Promise.all(
      Array.from({ length: 200 }, (_, i) =>
        withWriteWindow(doc, async () => {
          await wait(20 + (i % 50))
          if (i % 3 === 0) throw new Error('x')
        }).catch(() => undefined),
      ),
    )
    await wait(150)
    expect(timers()).to.be.at.most(base)
  })
})
