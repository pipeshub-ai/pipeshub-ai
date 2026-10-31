/**
 * The heartbeat of a writer window must stop whichever way the write ends (success, a throw, a throw with a failing end, a renew still
 * in flight) and leave no timer behind. Added by independent QA of STOR-FP-b: the existing U7 covers the success path only.
 */
import { expect } from 'chai'
import {
  withWriteWindow,
  writeWindowHooks,
} from '../../../../src/modules/storage/controllers/storage.write-window'

const realSetTimeout = global.setTimeout
const realClearTimeout = global.clearTimeout
const wait = (ms: number) => new Promise((r) => realSetTimeout(r, ms))

/**
 * Timers created by the writer window that have neither fired nor been cleared. A process-wide count also sees
 * other suites' clients (the Redis cluster job keeps live timers), so only timers created from the module under
 * test are tracked.
 */
function trackWindowTimers(): { pending: () => number; restore: () => void } {
  const live = new Set<NodeJS.Timeout>()
  global.setTimeout = ((fn: (...args: unknown[]) => void, ms?: number, ...args: unknown[]) => {
    if (!new Error().stack?.includes('storage.write-window.ts')) return realSetTimeout(fn, ms, ...args)
    const handle: NodeJS.Timeout = realSetTimeout(() => {
      live.delete(handle)
      fn(...args)
    }, ms)
    live.add(handle)
    return handle
  }) as typeof setTimeout
  global.clearTimeout = ((handle?: NodeJS.Timeout | string | number) => {
    live.delete(handle as NodeJS.Timeout)
    realClearTimeout(handle as NodeJS.Timeout)
  }) as typeof clearTimeout
  return {
    pending: () => live.size,
    restore: () => {
      global.setTimeout = realSetTimeout
      global.clearTimeout = realClearTimeout
    },
  }
}
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
      const tracked = trackWindowTimers()
      try {
        const { repo, calls } = fakeRepo(opts)
        writeWindowHooks.repo = () => repo as never
        await withWriteWindow(doc, work).catch(() => undefined)
        const atEnd = calls.renew
        await wait(200)
        expect(calls.renew, 'a renew started after the window ended').to.be.at.most(atEnd + 1)
        expect(calls.end).to.be.greaterThan(0)
        expect(tracked.pending(), 'timers left behind').to.equal(0)
      } finally {
        tracked.restore()
      }
    })
  }

  it('200 overlapping windows, a third of them failing, leave no timer', async () => {
    const tracked = trackWindowTimers()
    try {
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
      expect(tracked.pending()).to.equal(0)
    } finally {
      tracked.restore()
    }
  })
})
