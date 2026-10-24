import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'
import { createApiToolsetsRouter } from '../../../src/modules/api_toolsets/routes/api_toolsets.routes'

/**
 * Every API Toolsets route the gateway serves is in the published spec, and the spec
 * documents nothing the gateway doesn't serve. The router is mounted at
 * `/api/v1/api-toolsets`; spec paths are relative to `/api/v1`.
 */

const SPEC = join(__dirname, '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml')

type Spec = {
  paths: Record<string, Record<string, { tags?: string[]; security?: unknown[] }>>
}

const HTTP_METHODS = new Set(['get', 'post', 'put', 'patch', 'delete'])

function servedRoutes(): string[] {
  const container: any = {
    get: sinon.stub().callsFake((key: string) =>
      key === 'AuthMiddleware'
        ? { authenticate: (_req: unknown, _res: unknown, next: () => void) => next() }
        : { connectorBackend: 'http://localhost:8088' },
    ),
  }
  const router: any = createApiToolsetsRouter(container)
  return router.stack
    .filter((layer: any) => layer.route)
    .flatMap((layer: any) =>
      Object.keys(layer.route.methods).map(
        (method) => `${method} /api-toolsets${String(layer.route.path).replace(/:(\w+)/g, '{$1}')}`,
      ),
    )
    .sort()
}

function documentedRoutes(spec: Spec): string[] {
  return Object.entries(spec.paths)
    .filter(([path]) => path.startsWith('/api-toolsets'))
    .flatMap(([path, operations]) =>
      Object.keys(operations)
        .filter((method) => HTTP_METHODS.has(method))
        .map((method) => `${method} ${path}`),
    )
    .sort()
}

describe('API Toolsets routes match the spec', () => {
  const spec = yaml.load(readFileSync(SPEC, 'utf8')) as Spec

  afterEach(() => sinon.restore())

  it('documents exactly the routes the gateway serves', () => {
    expect(documentedRoutes(spec)).to.deep.equal(servedRoutes())
  })

  it('tags and secures every API Toolsets operation', () => {
    for (const [path, operations] of Object.entries(spec.paths)) {
      if (!path.startsWith('/api-toolsets')) continue
      for (const [method, operation] of Object.entries(operations)) {
        if (!HTTP_METHODS.has(method)) continue
        expect(operation.tags, `${method} ${path}`).to.deep.equal(['API Toolsets'])
        expect(operation.security, `${method} ${path}`).to.deep.equal([
          { bearerAuth: [] },
          { oauth2: [routeScopes()[`${method} ${path}`]] },
        ])
      }
    }
  })
})

/** `"<method> <spec path>"` to the scope its route requires, read from the router's source. */
function routeScopes(): Record<string, string> {
  const source = readFileSync(
    join(__dirname, '..', '..', '..', 'src', 'modules', 'api_toolsets', 'routes', 'api_toolsets.routes.ts'),
    'utf8',
  )
  const scopes: Record<string, string> = { read: 'connector:read', write: 'connector:write', remove: 'connector:delete' }
  const found: Record<string, string> = {}
  for (const m of source.matchAll(/router\.(get|post|put|delete)\(\s*'([^']+)',\s*authenticate,\s*(read|write|remove)\b/g)) {
    found[`${m[1]} /api-toolsets${m[2].replace(/:(\w+)/g, '{$1}')}`] = scopes[m[3]!]!
  }
  return found
}
