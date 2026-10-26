import fs from 'fs'
import path from 'path'
import sinon from 'sinon'
import { ValidationMiddleware } from '../../src/libs/middlewares/validation.middleware'

/**
 * Builds every router factory under src/modules with stand-in services and
 * returns each ValidationMiddleware mount (the real middleware function) with
 * the route it sits on, so a test can drive the real validation of the real
 * route schemas.
 */
const SRC = path.resolve(__dirname, '../../src')

const standIn = (): any =>
  new Proxy(function standInFn() {}, {
    get: (_t, prop) => {
      if (prop === 'then') return undefined
      if (prop === Symbol.toPrimitive) return () => ''
      if (prop === Symbol.iterator) return undefined
      return standIn()
    },
    apply: () => standIn(),
    construct: () => standIn(),
  })

const fakeContainer = (): any => ({
  get: () => standIn(),
  getAll: () => [],
  isBound: () => true,
  getNamed: () => standIn(),
  getTagged: () => standIn(),
})

interface Layer {
  route?: { path: string; methods: Record<string, boolean>; stack: Layer[] }
  handle: any
  regexp?: RegExp
}

const mountPrefix = (layer: Layer): string => {
  const source = layer.regexp?.source ?? ''
  const match = source.match(/^\^\\\/(.*?)\\\/\?\(\?=\\\/\|\$\)$/)
  return match?.[1] ? `/${match[1].replace(/\\\//g, '/')}` : ''
}

export interface ValidatorMount {
  key: string
  handler: (req: any, res: any, next: (arg?: unknown) => void) => Promise<void>
}

export async function collectValidatorMounts(): Promise<ValidatorMount[]> {
  const captured = new Map<unknown, true>()
  const original = ValidationMiddleware.validate.bind(ValidationMiddleware)
  process.env.SECRET_KEY = process.env.SECRET_KEY ?? 'route-validator-contract-key'
  const stub = sinon.stub(ValidationMiddleware, 'validate').callsFake((schema, options) => {
    const handler = original(schema, options)
    captured.set(handler, true)
    return handler
  })
  const mounts: ValidatorMount[] = []
  const seen = new Map<string, number>()
  const add = (key: string, handler: ValidatorMount['handler']): void => {
    const n = (seen.get(key) ?? 0) + 1
    seen.set(key, n)
    mounts.push({ key: n > 1 ? `${key} dup${n}` : key, handler })
  }
  const walk = (stack: Layer[], prefix: string, factory: string, inherited: any[]): void => {
    const routerWide = inherited.slice()
    for (const layer of stack) {
      if (layer.route) {
        const methods = Object.keys(layer.route.methods).filter((m) => layer.route!.methods[m]).map((m) => m.toUpperCase())
        const own = layer.route.stack.filter((l) => captured.has(l.handle)).map((l) => l.handle)
        let idx = 0
        for (const h of [...routerWide, ...own]) {
          add(`${factory} ${methods.join(',')} ${prefix}${layer.route.path} #${idx++}`, h)
        }
      } else if (Array.isArray(layer.handle?.stack)) {
        walk(layer.handle.stack, prefix + mountPrefix(layer), factory, routerWide)
      } else if (captured.has(layer.handle)) {
        routerWide.push(layer.handle)
      }
    }
  }
  try {
    const files = (fs.readdirSync(path.join(SRC, 'modules'), { recursive: true }) as string[])
      .filter((f) => /(^|\/)routes\/[^/]+\.ts$/.test(f) || /(^|\/)[^/]+\.routes\.ts$/.test(f))
      .map((f) => path.join(SRC, 'modules', f))
      .sort()
    for (const file of files) {
      const mod = (await import(file)) as Record<string, unknown>
      for (const [name, factory] of Object.entries(mod)) {
        if (typeof factory !== 'function' || !/^create\w*Router$/.test(name)) continue
        let router: { stack: Layer[] }
        try {
          router = (factory as (...c: unknown[]) => { stack: Layer[] })(fakeContainer(), fakeContainer())
        } catch {
          continue
        }
        walk(router.stack, '', name, [])
      }
    }
  } finally {
    stub.restore()
  }
  return mounts
}
