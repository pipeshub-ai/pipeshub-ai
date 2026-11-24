import 'reflect-metadata'
import { expect } from 'chai'
import { UNREADABLE_REQUEST_MESSAGE } from '../../src/libs/middlewares/validation.middleware'
import { ValidationError } from '../../src/libs/errors/validation.error'
import { collectValidatorMounts, ValidatorMount } from './route-validators'

/**
 * A schema callback (transform, refine, preprocess) that throws instead of adding a zod issue is answered with
 * the middleware's generic 400, which names no field and hides the bug. This drives every mounted route's real
 * validator with hostile values at every declared path, and fails when that generic 400 is reached by anything
 * other than an object whose `valueOf`/`toString` are not functions (a crafted query such as
 * `?page[valueOf]=1&page[toString]=1`, which no `Number(arg)` preprocess can convert), or when any value ends in
 * an error that is not a ValidationError.
 */

type Part = 'body' | 'query' | 'params' | 'headers'
type Path = string[]

const EXOTIC = (): unknown => JSON.parse('{"valueOf":"1","toString":"1"}')
const EXOTIC_LABELS = new Set(['exotic', '[exotic]'])

// Values a caller can send at each location. Params and headers are always strings.
const HOSTILE: Record<Part, Array<[string, () => unknown]>> = {
  body: [
    ['null', () => null],
    ['0', () => 0],
    ['true', () => true],
    ['"x"', () => 'x'],
    ['""', () => ''],
    ['long1001', () => 'a'.repeat(1001)],
    ['{}', () => ({})],
    ['[]', () => []],
    ['[null]', () => [null]],
    ['exotic', EXOTIC],
    ['[exotic]', () => [EXOTIC()]],
  ],
  query: [
    ['"x"', () => 'x'],
    ['""', () => ''],
    ['long1001', () => 'a'.repeat(1001)],
    ['["1","2"]', () => ['1', '2']],
    ['{a:"1"}', () => ({ a: '1' })],
    ['exotic', EXOTIC],
  ],
  params: [
    ['"x"', () => 'x'],
    ['long1001', () => 'a'.repeat(1001)],
  ],
  headers: [['"x"', () => 'x']],
}

/** Every path a schema declares; `[]` stands for an array element and `{}` for a record value. */
function declaredPaths(schema: any, path: Path, out: Path[], depth: number, seen: Set<unknown>): void {
  if (!schema?._zod || depth > 8) return
  const def = schema._zod.def
  out.push(path)
  if (seen.has(schema)) return
  seen.add(schema)
  switch (def.type) {
    case 'object':
      for (const key of Object.keys(def.shape)) declaredPaths(def.shape[key], [...path, key], out, depth + 1, seen)
      break
    case 'array':
      declaredPaths(def.element, [...path, '[]'], out, depth + 1, seen)
      break
    case 'record':
      declaredPaths(def.valueType, [...path, '{}'], out, depth + 1, seen)
      break
    case 'union':
      for (const option of def.options) declaredPaths(option, path, out, depth, seen)
      break
    case 'intersection':
      declaredPaths(def.left, path, out, depth, seen)
      declaredPaths(def.right, path, out, depth, seen)
      break
    case 'pipe':
      declaredPaths(def.in, path, out, depth, seen)
      declaredPaths(def.out, path, out, depth, seen)
      break
    case 'lazy':
      declaredPaths(def.getter(), path, out, depth, seen)
      break
    default:
      if (def.innerType) declaredPaths(def.innerType, path, out, depth, seen)
  }
  seen.delete(schema)
}

const nest = (path: Path, value: unknown): any => {
  if (path.length === 0) return value
  const [head, ...rest] = path
  if (head === '[]') return [nest(rest, value)]
  if (head === '{}') return { k: nest(rest, value) }
  return { [head]: nest(rest, value) }
}

const isGenericRefusal = (error: unknown): boolean =>
  error instanceof ValidationError &&
  error.errors.length === 1 &&
  error.errors[0]!.message === UNREADABLE_REQUEST_MESSAGE

describe('Schema callbacks: no mounted schema throws on caller input', () => {
  let mounts: ValidatorMount[] = []

  before(async function () {
    this.timeout(120000)
    mounts = await collectValidatorMounts()
  })

  it('reaches the generic 400 only for unconvertible objects, and never ends in a non-ValidationError', async function () {
    this.timeout(120000)
    let calls = 0
    const netForOrdinaryValue: string[] = []
    const notValidationError: string[] = []
    for (const mount of mounts) {
      const paths: Path[] = []
      declaredPaths(mount.schema, [], paths, 0, new Set())
      const unique = [...new Map(paths.filter((p) => p.length > 0).map((p) => [p.join('.'), p])).values()]
      for (const path of unique) {
        const values = HOSTILE[path[0] as Part]
        if (!values) continue
        for (const [label, make] of values) {
          const req = {
            body: {},
            query: {},
            params: {},
            headers: {},
            ...nest(path, make()),
            path: '/x',
            method: 'POST',
          }
          let got: unknown
          calls += 1
          await mount.handler(req, {}, (arg?: unknown) => {
            got = arg
          })
          const where = `${mount.key} @${path.join('.')}=${label}`
          if (got !== undefined && !(got instanceof ValidationError)) {
            notValidationError.push(`${where} -> ${(got as Error)?.constructor?.name}`)
          } else if (isGenericRefusal(got) && !EXOTIC_LABELS.has(label)) {
            netForOrdinaryValue.push(where)
          }
        }
      }
    }

    // 38,134 calls on 2026-11-24: a much lower count means the path walk lost schemas
    expect(calls, 'the guard drove too few cases').to.be.greaterThan(30000)
    expect(notValidationError, 'a value ended in an error that is not a ValidationError').to.deep.equal([])
    expect(
      netForOrdinaryValue,
      'a schema callback threw on an ordinary value; report it with ctx.addIssue instead',
    ).to.deep.equal([])
  })
})
