import 'reflect-metadata'
import { expect } from 'chai'
import crypto from 'crypto'
import fs from 'fs'
import path from 'path'
import sinon from 'sinon'
import { Logger } from '../../src/libs/services/logger.service'
import { ErrorMiddleware } from '../../src/libs/middlewares/error.middleware'
import { collectValidatorMounts, ValidatorMount } from './route-validators'

/**
 * VALIDATION CONTRACT SNAPSHOT
 * ----------------------------
 * What a client sees from request validation is part of the public API: the
 * status, the `errors[]` (code, field, message) and the body that reaches the
 * controller. This test replays a fixed set of payloads (generated once, seed
 * 918273, from the schemas of the zod 3 code base) through the real
 * ValidationMiddleware of every route that has one, then through the real
 * ErrorMiddleware, and compares with the results recorded on that code base
 * (validation-contract.fixture.json, "expected").
 *
 * Comparison rules
 *  - status, error code, and the multiset of [code, field, message] must match;
 *    the ORDER of errors is not compared: zod 3 listed them in the order its
 *    async parser finished each branch, zod 4 lists them in schema order.
 *  - an accepted request must reach the controller with an identical
 *    body/query/params (compared by hash).
 *  - a case listed in the fixture's "intentional" map is allowed to differ and
 *    must still differ (an entry that no longer differs is stale and fails).
 *
 * Besides the hand-picked payloads every route has four derived cases (the
 * valid request without a body / with an empty body, query or params), so a
 * field that stops being optional shows up as a failure here.
 *
 * WHEN YOU CHANGE VALIDATION ON PURPOSE
 *  1. Run the test, read the mismatches it prints.
 *  2. If the change is deliberate, re-record:
 *       UPDATE_VALIDATION_CONTRACT=1 TS_NODE_PROJECT=tsconfig.test.json \
 *         npx mocha --no-parallel tests/contract/validation-contract.test.ts
 *     (--no-parallel; the mocharc spec glob runs the whole suite otherwise, so
 *     pass --spec or use the repo's `npm test -- --grep "validation contract"`).
 *     That rewrites "expected" for every case and clears "intentional".
 *  3. Review `git diff` of the fixture and say in the PR why each change is OK.
 *  A new route is picked up automatically on re-record (generic payloads are
 *  added for it); until then this test fails and names it.
 */
const FIXTURE = path.join(__dirname, 'validation-contract.fixture.json')

interface Expected {
  ok?: string
  status?: number
  code?: string
  message?: string
  errors?: Array<[string, string, string]>
}
type Payload = { body?: unknown; query?: unknown; params?: unknown; headers?: unknown }
/**
 * Cases built from the route's own "valid" case, so the fixture stays small:
 * the request that is fully valid, minus one part. They catch a field that
 * used to be optional becoming required (zod 4 does that to `z.any()`), which
 * no hand-picked invalid payload would.
 */
const DERIVED = {
  'body-absent': (p: Payload): Payload => {
    const { body: _body, ...rest } = p
    return rest
  },
  'body-empty': (p: Payload): Payload => ({ ...p, body: {} }),
  'query-empty': (p: Payload): Payload => ({ ...p, query: {} }),
  'params-empty': (p: Payload): Payload => ({ ...p, params: {} }),
} as const
type DerivedName = keyof typeof DERIVED
interface Case {
  id: string
  payload?: Payload
  /** Payload = the route's "valid" case with this change. */
  derive?: DerivedName
  expected?: Expected
}
interface Fixture {
  note: string
  seed: number
  intentional: Record<string, string>
  routes: Array<{ key: string; cases: Case[] }>
}

const GENERIC_PAYLOADS: Array<[string, Payload]> = [
  ['empty-all', {}],
  ['body={}', { body: {} }],
  ['body=[]', { body: [] }],
  ['body="s"', { body: 's' }],
]

const encode = (value: unknown): string =>
  JSON.stringify(value, function (this: any, key: string, v: unknown) {
    const raw = this[key]
    if (raw instanceof Date) return { $date: Number.isNaN(raw.getTime()) ? 'Invalid' : raw.toISOString() }
    if (v === undefined) return { $undef: 1 }
    if (typeof v === 'number' && !Number.isFinite(v)) return { $num: String(v) }
    if (typeof v === 'bigint') return { $big: String(v) }
    return v
  })

const clone = <T>(value: T): T => (value === undefined ? value : JSON.parse(JSON.stringify(value)))

async function run(mount: ValidatorMount, payload: Payload): Promise<Expected> {
  const p: any = clone(payload) ?? {}
  const req: any = {
    body: p.body,
    query: p.query === undefined ? {} : p.query,
    params: p.params === undefined ? {} : p.params,
    headers: p.headers ?? { 'content-type': 'application/json' },
    path: '/contract',
    method: 'POST',
  }
  let nextArg: any = 'NOT_CALLED'
  await mount.handler(req, {}, (arg?: unknown) => {
    nextArg = arg
  })
  if (nextArg === undefined) {
    const hash = crypto
      .createHash('sha1')
      .update(encode({ body: req.body, query: req.query, params: req.params }))
      .digest('hex')
      .slice(0, 16)
    return { ok: hash }
  }
  if (nextArg === 'NOT_CALLED') return { status: -1 }
  const res: any = {
    statusCode: 0,
    body: null,
    headersSent: false,
    setHeader() {},
    status(s: number) {
      this.statusCode = s
      return this
    },
    json(b: unknown) {
      this.body = b
      return this
    },
  }
  ErrorMiddleware.handleError()(nextArg, req, res, () => {})
  return {
    status: res.statusCode,
    code: res.body?.error?.code,
    message: res.body?.error?.message,
    errors: (nextArg.errors ?? []).map((e: any) => [e.code, e.field, e.message]),
  }
}

/** One case per line, so a re-record reads as a reviewable diff. */
function writeFixture(f: Fixture): void {
  const lines: string[] = []
  lines.push(`{"note":${JSON.stringify(f.note)},"seed":${f.seed},"intentional":${JSON.stringify(f.intentional)},"routes":[`)
  f.routes.forEach((route, i) => {
    lines.push(`{"key":${JSON.stringify(route.key)},"cases":[`)
    route.cases.forEach((c, j) => lines.push(JSON.stringify(c) + (j < route.cases.length - 1 ? ',' : '')))
    lines.push(']}' + (i < f.routes.length - 1 ? ',' : ''))
  })
  lines.push(']}')
  fs.writeFileSync(FIXTURE, lines.join('\n') + '\n')
}

const sortedErrors = (e?: Expected['errors']): string[] => (e ?? []).map((t) => JSON.stringify(t)).sort()
const sortedLines = (m?: string): string[] => (m ?? '').split('\n').sort()

function sameContract(a: Expected, b: Expected): boolean {
  if (a.ok !== undefined || b.ok !== undefined) return a.ok === b.ok
  return (
    a.status === b.status &&
    a.code === b.code &&
    JSON.stringify(sortedErrors(a.errors)) === JSON.stringify(sortedErrors(b.errors)) &&
    JSON.stringify(sortedLines(a.message)) === JSON.stringify(sortedLines(b.message))
  )
}

describe('validation contract snapshot', function () {
  this.timeout(180000)
  const update = process.env.UPDATE_VALIDATION_CONTRACT === '1'
  let mounts: ValidatorMount[] = []
  let fixture: Fixture

  before(async () => {
    const logger = Logger.getInstance()
    for (const m of ['info', 'debug', 'error', 'warn'] as const) sinon.stub(logger, m)
    fixture = JSON.parse(fs.readFileSync(FIXTURE, 'utf8')) as Fixture
    mounts = await collectValidatorMounts()
  })
  after(() => sinon.restore())

  it('has a recorded case for every route that validates', () => {
    const recorded = new Set(fixture.routes.map((r) => r.key))
    const missing = mounts.map((m) => m.key).filter((k) => !recorded.has(k))
    if (update) return
    expect(missing, 'routes without recorded cases (re-record, see the top of this file)').to.deep.equal([])
  })

  it('replays every recorded payload with the recorded result (or a listed intentional difference)', async () => {
    const byKey = new Map(mounts.map((m) => [m.key, m]))
    const mismatches: string[] = []
    const stillDiffering = new Set<string>()
    let replayed = 0

    if (update) {
      const known = new Set(fixture.routes.map((r) => r.key))
      for (const m of mounts) {
        if (!known.has(m.key)) {
          fixture.routes.push({ key: m.key, cases: GENERIC_PAYLOADS.map(([label, payload]) => ({ id: `${m.key} ${label}`, payload })) })
        }
      }
    }

    if (update) {
      for (const route of fixture.routes) {
        for (const name of Object.keys(DERIVED) as DerivedName[]) {
          if (!route.cases.some((c) => c.derive === name)) {
            route.cases.push({ id: `${route.key} ${name}`, derive: name })
          }
        }
      }
    }

    for (const route of fixture.routes) {
      const valid = route.cases.find((c) => c.payload && c.id.endsWith(' valid'))?.payload
      const mount = byKey.get(route.key)
      if (!mount) {
        if (!update) mismatches.push(`${route.key}: route no longer validates`)
        continue
      }
      for (const c of route.cases) {
        let payload: Payload | undefined = c.payload
        if (c.derive) {
          payload = DERIVED[c.derive](valid ?? {})
        }
        const actual = await run(mount, payload ?? {})
        replayed++
        if (update) {
          c.expected = actual
          continue
        }
        if (!sameContract(c.expected as Expected, actual)) {
          stillDiffering.add(c.id)
          if (!(c.id in fixture.intentional)) {
            mismatches.push(`${c.id}\n    recorded: ${JSON.stringify(c.expected)}\n    now:      ${JSON.stringify(actual)}`)
          }
        }
      }
    }

    if (update) {
      fixture.intentional = {}
      fixture.routes = fixture.routes.filter((r) => byKey.has(r.key))
      writeFixture(fixture)
      return
    }
    const stale = Object.keys(fixture.intentional).filter((id) => !stillDiffering.has(id))
    expect(replayed).to.be.greaterThan(1000)
    expect(mismatches.slice(0, 20).join('\n'), `${mismatches.length} client-visible validation changes`).to.equal('')
    expect(stale, 'intentional entries that no longer differ: remove them').to.deep.equal([])
  })
})
