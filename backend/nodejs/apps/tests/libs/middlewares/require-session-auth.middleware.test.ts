import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { requireSessionAuth } from '../../../src/libs/middlewares/require-session-auth.middleware'
import { ForbiddenError, UnauthorizedError } from '../../../src/libs/errors/http.errors'

function createMockRequest(user?: Record<string, any>): any {
  return { headers: {}, body: {}, params: {}, query: {}, user }
}

describe('requireSessionAuth Middleware', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('should call next with UnauthorizedError when req.user is undefined', () => {
    const next = sinon.stub()
    requireSessionAuth(createMockRequest(), {} as any, next)
    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(UnauthorizedError)
  })

  it('should pass through a session JWT user (no isOAuth flag)', () => {
    const next = sinon.stub()
    requireSessionAuth(
      createMockRequest({ userId: 'u1', orgId: 'o1', role: 'member' }),
      {} as any,
      next,
    )
    expect(next.calledOnceWithExactly()).to.be.true
  })

  it('should pass through when isOAuth is explicitly false', () => {
    const next = sinon.stub()
    requireSessionAuth(
      createMockRequest({ userId: 'u1', orgId: 'o1', isOAuth: false }),
      {} as any,
      next,
    )
    expect(next.calledOnceWithExactly()).to.be.true
  })

  it('should reject an OAuth access token user with ForbiddenError (403)', () => {
    const next = sinon.stub()
    requireSessionAuth(
      createMockRequest({
        userId: 'u1',
        orgId: 'o1',
        role: 'admin',
        isOAuth: true,
        oauthClientId: 'client-1',
        oauthScopes: ['org:admin'],
      }),
      {} as any,
      next,
    )
    expect(next.calledOnce).to.be.true
    const err = next.firstCall.args[0]
    expect(err).to.be.instanceOf(ForbiddenError)
    expect(err.statusCode).to.equal(403)
  })

  it('should reject a personal access token user (PATs authenticate as OAuth tokens)', () => {
    const next = sinon.stub()
    requireSessionAuth(
      createMockRequest({
        userId: 'u1',
        orgId: 'o1',
        isOAuth: true,
        oauthClientId: 'pat-system:o1',
        oauthScopes: ['org:read'],
      }),
      {} as any,
      next,
    )
    expect(next.calledOnce).to.be.true
    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })
})

describe('the allowlist guard used by connector and toolset write routes', () => {
  const load = () =>
    require('../../../src/libs/middlewares/require-session-auth.middleware') as typeof import('../../../src/libs/middlewares/require-session-auth.middleware')

  it('isSetValue ignores key names: anything with an entry or a scalar sets a value', () => {
    const { isSetValue } = load()
    for (const v of [{ oauthConfigId: 'x' }, { a: { b: 1 } }, { k: null }, [1], 's', '', 0, false, true]) {
      expect(isSetValue(v), JSON.stringify(v)).to.equal(true)
    }
    for (const v of [undefined, null, {}, []]) {
      expect(isSetValue(v), JSON.stringify(v)).to.equal(false)
    }
  })

  const guard = () => {
    const { requireSessionAuthExcept } = load()
    return requireSessionAuthExcept(
      { sync: true, filters: true, config: ['sync', 'filters'] },
      { emptyAllowed: ['auth', 'config.auth'] },
    )
  }
  const run = (body: unknown, isOAuth: boolean) => {
    let result: unknown = 'not called'
    guard()({ user: { isOAuth }, body } as never, {} as never, (e?: unknown) => { result = e ?? null })
    return result as { statusCode?: number } | null
  }

  it('refuses a token for every key outside the allowlist, whatever it is called', () => {
    for (const body of [
      { auth: { oauthConfigId: 'other-app' } },
      { auth: { clientSecret: 's' } },
      { oauthConfigId: 'other-app' },
      { baseUrl: 'https://evil.example' },
      { baseUrl: '' },
      { clientId: 'x', clientSecret: 'y' },
      { aKeyNoListHasSeen: 1 },
      { oauthConfigId: 0 },
      { flag: false },
      { auth: { x: null } },
      { auth: 'a bare string' },
      { config: { auth: { oauthConfigId: 'other-app' } } },
      { config: 'a bare string' },
      { sync: {}, other: [null] },
      ['x'],
    ]) {
      expect(run(body, true)?.statusCode, JSON.stringify(body)).to.equal(403)
    }
  })

  it('refuses an off-list key whenever it is present, even empty', () => {
    for (const body of [
      { authConfig: {} },
      { other: [] },
      { other: null },
      { oauthConfigId: null },
      { baseUrl: null },
      { config: { other: {} } },
    ]) {
      expect(run(body, true)?.statusCode, JSON.stringify(body)).to.equal(403)
    }
  })

  it('lets a token send allowed keys, and the listed keys when they are empty', () => {
    for (const body of [
      {},
      { sync: { selectedStrategy: 'SCHEDULED' }, filters: { sync: {} } },
      { config: { sync: { x: 1 }, filters: {} } },
      { auth: {} },
      { auth: null },
      { auth: [] },
      { config: { auth: {} } },
      { config: [] },
      { baseUrl: undefined },
    ]) {
      expect(run(body, true), JSON.stringify(body)).to.equal(null)
    }
  })

  it('lets a session send anything, and refuses a request with no user', () => {
    expect(run({ auth: { clientSecret: 's' }, baseUrl: 'https://x' }, false)).to.equal(null)
    let result: unknown = 'not called'
    guard()({ body: {} } as never, {} as never, (e?: unknown) => { result = e })
    expect((result as { statusCode?: number }).statusCode).to.equal(401)
  })

  it('does not treat inherited keys such as constructor as allowed', () => {
    const { requireSessionAuthExcept } = load()
    let result: unknown = 'not called'
    requireSessionAuthExcept({})({ user: { isOAuth: true }, body: JSON.parse('{"constructor": "x"}') } as never, {} as never, (e?: unknown) => { result = e ?? null })
    expect((result as { statusCode?: number }).statusCode).to.equal(403)
  })

  describe('a body whose prototype carries properties (what the sanitizer used to build from __proto__)', () => {
    const inherited = () => ({ authConfig: { clientId: 'x', clientSecret: 'y' } })
    const withProto = (own: Record<string, unknown>) => Object.assign(Object.create(inherited()), own)

    it('refuses a body with a foreign prototype, with or without own keys', () => {
      expect(run(withProto({ instanceName: 'a' }), true)?.statusCode).to.equal(403)
      expect(run(Object.create(inherited()), true)?.statusCode).to.equal(403)
    })

    it('refuses a nested allowlisted object, or an empty-allowed one, with a foreign prototype', () => {
      expect(run({ config: withProto({ sync: {} }) }, true)?.statusCode).to.equal(403)
      expect(run({ config: Object.create(inherited()) }, true)?.statusCode).to.equal(403)
      expect(run({ auth: Object.create(inherited()) }, true)?.statusCode).to.equal(403)
    })

    it('refuses a foreign prototype under a key that takes any value, at every depth', () => {
      expect(run({ sync: Object.create(inherited()) }, true)?.statusCode).to.equal(403)
      expect(run({ sync: { a: { b: [withProto({ c: 1 })] } } }, true)?.statusCode).to.equal(403)
      expect(run({ filters: [[Object.create(inherited())]] }, true)?.statusCode).to.equal(403)
      expect(run({ sync: { a: { b: [{ c: 1 }, null, 'x'] } } }, true)).to.equal(null)
    })

    it('refuses values nested deeper than it walks under a key that takes any value', () => {
      let deep: Record<string, unknown> = { leaf: 1 }
      for (let i = 0; i < 40; i++) deep = { next: deep }
      expect(run({ sync: deep }, true)?.statusCode).to.equal(403)
    })

    it('lets a null-prototype body through when its own keys are allowed', () => {
      expect(run(Object.assign(Object.create(null), { sync: {} }), true)).to.equal(null)
    })

    it('refuses an own __proto__ key, as express.json produces it', () => {
      const body = JSON.parse('{"sync":{},"__proto__":{"authConfig":{"clientId":"x"}}}')
      expect(run(body, true)?.statusCode).to.equal(403)
    })

    it('the review body, after the real sanitizer, carries no inherited authConfig and has nothing left to refuse', () => {
      const { xssSanitizationMiddleware } = require('../../../src/libs/middlewares/xss-sanitization.middleware') as typeof import('../../../src/libs/middlewares/xss-sanitization.middleware')
      const raw = '{"instanceName":"a","toolsetType":"slack","authType":"OAUTH","__proto__":{"authConfig":{"clientId":"x","clientSecret":"y"}}}'
      const req: any = { path: '/api/v1/toolsets/instances', method: 'POST', body: JSON.parse(raw), query: {}, params: {}, headers: {} }
      xssSanitizationMiddleware(req, {} as never, () => {})
      // The sanitizer now drops the key, so the body has no authConfig at all; the guard
      // refusing a foreign prototype above is the second layer for any other way to build one.
      expect(Object.keys(req.body)).to.deep.equal(['instanceName', 'toolsetType', 'authType'])
      expect(req.body.authConfig).to.equal(undefined)
      const { requireSessionAuthExcept } = load()
      let result: unknown = 'not called'
      requireSessionAuthExcept({ instanceName: true, toolsetType: true, authType: true, scope: true })(
        { ...req, user: { isOAuth: true } } as never, {} as never, (e?: unknown) => { result = e ?? null },
      )
      expect(result).to.equal(null)
    })
  })
})
