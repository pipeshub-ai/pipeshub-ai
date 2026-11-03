import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'
import { createMcpServersRouter } from '../../../src/modules/mcp_servers/routes/mcp_servers.routes'

/**
 * Every MCP server route the gateway serves is in the published spec, and the spec
 * documents nothing the gateway doesn't serve. The router is mounted at
 * `/api/v1/mcp-servers`; spec paths are relative to `/api/v1`.
 */

const SPEC = join(__dirname, '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml')

type Spec = {
  paths: Record<string, Record<string, { tags?: string[]; security?: unknown[] }>>
}

/** The router under test, with authentication stubbed to pass. */
function router(): any {
  const container: any = {
    get: sinon.stub().callsFake((key: string) =>
      key === 'AuthMiddleware'
        ? { authenticate: (_req: unknown, _res: unknown, next: () => void) => next() }
        : { connectorBackend: 'http://localhost:8088' },
    ),
  }
  return createMcpServersRouter(container)
}

/** An Express route as `method /mcp-servers/{param}`, the spec's path form. */
const routeId = (method: string, path: string) =>
  `${method} /mcp-servers${path.replace(/:(\w+)/g, '{$1}')}`

/** Every `method /mcp-servers...` route the router serves, in spec path form. */
function servedRoutes(): string[] {
  return router()
    .stack.filter((layer: any) => layer.route)
    .flatMap((layer: any) => Object.keys(layer.route.methods).map((method) => routeId(method, String(layer.route.path))))
    .sort()
}

/** The scopes each route's requireScopes accepts, read from its refusal of an OAuth token without any. */
function gateScopes(): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  for (const layer of router().stack.filter((l: any) => l.route)) {
    let refusal: Error | undefined
    for (const handler of layer.route.stack.slice(0, 2)) {
      handler.handle({ user: { isOAuth: true, oauthScopes: [] } }, {}, (err?: Error) => (refusal = err))
      if (refusal) break
    }
    const required = refusal?.message.match(/^Insufficient scope\. Required: (.+)$/)?.[1]
    for (const method of Object.keys(layer.route.methods)) {
      out[routeId(method, String(layer.route.path))] = required ? required.split(' or ') : []
    }
  }
  return out
}

/** Every `method path` the spec documents under `/mcp-servers`. */
function documentedRoutes(spec: Spec): string[] {
  return Object.entries(spec.paths)
    .filter(([path]) => path.startsWith('/mcp-servers'))
    .flatMap(([path, operations]) => Object.keys(operations).map((method) => `${method} ${path}`))
    .sort()
}

describe('MCP server routes match the spec', () => {
  const spec = yaml.load(readFileSync(SPEC, 'utf8')) as Spec

  afterEach(() => sinon.restore())

  it('documents exactly the routes the gateway serves', () => {
    expect(documentedRoutes(spec)).to.deep.equal(servedRoutes())
  })

  it('tags every MCP operation and lists the scopes its gate accepts', () => {
    const scopes = gateScopes()
    for (const [path, operations] of Object.entries(spec.paths)) {
      if (!path.startsWith('/mcp-servers')) continue
      for (const [method, operation] of Object.entries(operations)) {
        const id = `${method} ${path}`
        expect(operation.tags?.[0], id).to.match(/^MCP /)
        expect(scopes[id], id).to.not.be.empty
        expect(operation.security, id).to.deep.equal([
          { bearerAuth: [] },
          ...scopes[id]!.map((scope) => ({ oauth2: [scope] })),
        ])
      }
    }
  })
})
