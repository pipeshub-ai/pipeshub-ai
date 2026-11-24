import sinon from 'sinon'
import winston from 'winston'
import { runWithRequestContext } from '../../src/libs/context/request-context'

/** One record as winston receives it: `message`, `level`, the typed fields, `request_id`, `metadata`. */
export type LogRecord = Record<string, any>

/**
 * Every record winston would write while a test runs: every logger (whichever root a module took at load) and
 * every level. Call it in a `beforeEach`; `sinon.restore()` removes the stubs. The list holds the whole process's
 * lines, a timer another test left running included: assert on `recordsOf(records, id)`, never on the list itself.
 */
export function captureLogRecords(): LogRecord[] {
  const records: LogRecord[] = []
  sinon.stub(winston.Logger.prototype, 'isLevelEnabled').returns(true)
  sinon.stub(winston.Logger.prototype, 'log').callsFake(function (this: unknown, info: LogRecord) {
    records.push(info)
    return this
  } as never)
  return records
}

let sequence = 0

/** A request id no other test uses: send it as `x-request-id`, or run code under it with `inRequestContext`. */
export function newRequestId(prefix = 'test'): string {
  sequence += 1
  return `${prefix}-${process.pid}-${sequence}`
}

/** Runs `fn` as the request `requestId`, for code a test calls without an HTTP request (a consumer, a producer). */
export function inRequestContext<T>(requestId: string, fn: () => T): T {
  return runWithRequestContext({ rootId: requestId }, fn)
}

/**
 * The records written in the context whose root id is `requestId`: the request's own lines, the background work
 * it started included. A leftover timer logs in its own context, or in none, and is not counted, whatever it says.
 */
export function recordsOf(records: readonly LogRecord[], requestId: string): LogRecord[] {
  return records.filter((record) => record.request_id === requestId)
}
