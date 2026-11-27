import { expect } from 'chai'
import sinon from 'sinon'

/**
 * Runs `start` on a fake clock (`Date` and the timers) and checks the order a deadline promises: what it returns is
 * still pending one millisecond before `ms`, and settles once `ms` have passed, with no more time. A busy host cannot
 * break that order, where a bound on the host's clock fails a gate that runs slowly. A deadline that came later
 * leaves the promise pending, and the test's own timeout ends it.
 */
export async function settlesAt<T>(ms: number, start: () => Promise<T>): Promise<T> {
  const clock = sinon.useFakeTimers({ now: Date.now(), toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  try {
    let settled = false
    const pending = start()
    pending.then(
      () => (settled = true),
      () => (settled = true),
    )
    await clock.tickAsync(ms - 1)
    expect(settled, `settled before its ${String(ms)} ms deadline`).to.equal(false)
    await clock.tickAsync(1)
    return await pending
  } finally {
    clock.restore()
  }
}
