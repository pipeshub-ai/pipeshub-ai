import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'
import { createArtifactsRouter } from '../../../src/modules/artifacts/routes/artifacts.routes'
import { ForbiddenError } from '../../../src/libs/errors/http.errors'

/**
 * Every artifact route the gateway serves is in the published spec, and the
 * spec documents nothing it doesn't serve. The router is mounted at
 * `/api/v1/artifacts`; spec paths are relative to `/api/v1`.
 */

const SPEC = join(__dirname, '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml')

type Operation = {
  operationId?: string
  security?: Record<string, string[]>[]
  'x-pipeshub-sdk'?: boolean
  responses?: Record<string, unknown>
}
type Spec = { paths: Record<string, Record<string, Operation>> }

/** The router under test, with authentication stubbed to pass. */
function router(): any {
  const container: any = {
    get: sinon.stub().callsFake((key: string) =>
      key === 'AuthMiddleware'
        ? { authenticate: (_req: unknown, _res: unknown, next: () => void) => next() }
        : { connectorBackend: 'http://localhost:8088' },
    ),
  }
  return createArtifactsRouter(container)
}

/** Every `method /artifacts...` route the router serves, in spec path form. */
function servedRoutes(): string[] {
  return router()
    .stack.filter((layer: any) => layer.route)
    .flatMap((layer: any) =>
      Object.keys(layer.route.methods).map(
        (method) => `${method} /artifacts${String(layer.route.path).replace(/:(\w+)/g, '{$1}').replace(/\/$/, '')}`,
      ),
    )
    .sort()
}

/** Every `method path` the spec documents under `/artifacts`. */
function documentedRoutes(spec: Spec): string[] {
  return Object.entries(spec.paths)
    .filter(([path]) => path === '/artifacts' || path.startsWith('/artifacts/'))
    .flatMap(([path, operations]) => Object.keys(operations).map((method) => `${method} ${path}`))
    .sort()
}

/** The requireScopes middleware of one route. */
function scopeCheckOf(method: string, path: string): (req: any, res: any, next: sinon.SinonStub) => void {
  const layer = router().stack.find(
    (l: any) => l.route && l.route.path === path && l.route.methods[method],
  )
  // authenticate, then requireScopes, then validation, then the handler.
  return layer.route.stack[1].handle
}

describe('Artifact routes match the spec', () => {
  const spec = yaml.load(readFileSync(SPEC, 'utf8')) as Spec

  afterEach(() => sinon.restore())

  it('documents exactly the routes the gateway serves', () => {
    expect(documentedRoutes(spec)).to.deep.equal(servedRoutes())
  })

  it('documents delete as conversation:write and keeps it out of the generated SDK and MCP tools', () => {
    const operation = spec.paths['/artifacts/{artifactId}']?.delete
    expect(operation?.operationId).to.equal('deleteArtifact')
    expect(operation?.security).to.deep.equal([{ bearerAuth: [] }, { oauth2: ['conversation:write'] }])
    expect(operation?.['x-pipeshub-sdk']).to.equal(undefined)
    // 400 and 500 are the app-wide markup filter and body parsers, answered before this route runs.
    expect(Object.keys(operation?.responses ?? {}).sort()).to.deep.equal(['204', '400', '401', '403', '404', '500', '503'])
  })

  it('documents the caller role on gallery items', () => {
    const schemas = (yaml.load(readFileSync(SPEC, 'utf8')) as any).components.schemas
    expect(schemas.ArtifactGalleryItem.properties).to.have.property('role')
  })
})

describe('Artifact delete scopes', () => {
  const check = (oauthScopes: string[]) => {
    const next = sinon.stub()
    scopeCheckOf('delete', '/:artifactId')({ user: { isOAuth: true, oauthScopes } }, {}, next)
    return next
  }

  it('admits an OAuth token with conversation:write', () => {
    const next = check(['conversation:write'])
    expect(next.calledOnceWithExactly()).to.equal(true)
  })

  it('refuses an OAuth token with only kb or connector delete scopes', () => {
    const next = check(['kb:delete', 'connector:delete', 'kb:read'])
    expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError)
  })
})
