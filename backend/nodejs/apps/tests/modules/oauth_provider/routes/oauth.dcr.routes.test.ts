import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { createOAuthDcrRouter } from '../../../../src/modules/oauth_provider/routes/oauth.dcr.routes'

function getRouteLayer(router: any, path: string, method: 'get' | 'post' | 'put' | 'delete') {
  return router.stack.find(
    (layer: any) => layer.route?.path === path && layer.route?.methods?.[method],
  )?.route
}

describe('OAuth DCR Routes', () => {
  afterEach(() => {
    sinon.restore()
  })

  it('registers GET/PUT/DELETE with shared management limiter before authenticate', () => {
    const authenticate = sinon.stub()
    const controller = {
      register: sinon.stub(),
      getRegistration: sinon.stub(),
      updateRegistration: sinon.stub(),
      deleteRegistration: sinon.stub(),
    }
    const mockContainer = {
      get: sinon.stub().callsFake((key: string) => {
        if (key === 'OAuthDcrController') return controller
        if (key === 'OAuthRegistrationTokenMiddleware') return { authenticate }
        if (key === 'Logger') {
          return {
            info: sinon.stub(),
            warn: sinon.stub(),
            error: sinon.stub(),
            debug: sinon.stub(),
          }
        }
        if (key === 'AppConfig') {
          return {
            maxOAuthClientRequestsPerMinute: 100,
            maxDcrRegistrationsPerMinute: 5,
          }
        }
        return {}
      }),
    }

    const router = createOAuthDcrRouter(mockContainer as any)
    const getRoute = getRouteLayer(router, '/register/:client_id', 'get')
    const putRoute = getRouteLayer(router, '/register/:client_id', 'put')
    const deleteRoute = getRouteLayer(router, '/register/:client_id', 'delete')

    expect(getRoute).to.exist
    expect(putRoute).to.exist
    expect(deleteRoute).to.exist

    const getHandles = getRoute.stack.map((layer: any) => layer.handle)
    const putHandles = putRoute.stack.map((layer: any) => layer.handle)
    const deleteHandles = deleteRoute.stack.map((layer: any) => layer.handle)

    const commonMgmtHandles = getHandles.filter(
      (h: any) =>
        putHandles.includes(h) &&
        deleteHandles.includes(h) &&
        h !== authenticate,
    )
    expect(commonMgmtHandles.length).to.be.greaterThan(0)
    const dcrMgmtRateLimiter = commonMgmtHandles[0]

    expect(getHandles.includes(dcrMgmtRateLimiter)).to.be.true
    expect(getHandles.indexOf(dcrMgmtRateLimiter)).to.be.lessThan(
      getHandles.indexOf(authenticate),
    )
    expect(putHandles.indexOf(dcrMgmtRateLimiter)).to.be.lessThan(
      putHandles.indexOf(authenticate),
    )
    expect(deleteHandles.indexOf(dcrMgmtRateLimiter)).to.be.lessThan(
      deleteHandles.indexOf(authenticate),
    )
  })

  it('uses a dedicated limiter on POST /register distinct from management limiter', () => {
    const authenticate = sinon.stub()
    const controller = {
      register: sinon.stub(),
      getRegistration: sinon.stub(),
      updateRegistration: sinon.stub(),
      deleteRegistration: sinon.stub(),
    }
    const mockContainer = {
      get: sinon.stub().callsFake((key: string) => {
        if (key === 'OAuthDcrController') return controller
        if (key === 'OAuthRegistrationTokenMiddleware') return { authenticate }
        if (key === 'Logger') {
          return {
            info: sinon.stub(),
            warn: sinon.stub(),
            error: sinon.stub(),
            debug: sinon.stub(),
          }
        }
        if (key === 'AppConfig') {
          return {
            maxOAuthClientRequestsPerMinute: 100,
            maxDcrRegistrationsPerMinute: 5,
          }
        }
        return {}
      }),
    }

    const router = createOAuthDcrRouter(mockContainer as any)
    const postRoute = getRouteLayer(router, '/register', 'post')
    const getRoute = getRouteLayer(router, '/register/:client_id', 'get')

    expect(postRoute).to.exist
    expect(getRoute).to.exist

    const postHandles = postRoute.stack.map((layer: any) => layer.handle)
    const getHandles = getRoute.stack.map((layer: any) => layer.handle)

    const sharedHandle = getHandles.find((h: any) => h !== authenticate && postHandles.includes(h))
    // POST should not reuse the management limiter used on RFC 7592 routes.
    expect(sharedHandle).to.be.undefined
  })
})
