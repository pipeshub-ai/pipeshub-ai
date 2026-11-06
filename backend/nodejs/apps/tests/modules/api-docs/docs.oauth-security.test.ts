import { expect } from 'chai'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'

// The SDK generator refuses an operation with more than one OAuth2 requirement ("multiple
// security requirements for OAuth2 scheme ... with different scopes"), which fails every SDK
// build. A route that takes either of two scopes names the smaller one and says so in its 403.
describe('published spec security', () => {
  const spec: any = yaml.load(
    readFileSync(join(__dirname, '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'), 'utf8'),
  )

  it('gives every operation at most one OAuth2 requirement', () => {
    const repeated: string[] = []
    for (const [path, ops] of Object.entries<any>(spec.paths)) {
      for (const [method, op] of Object.entries<any>(ops)) {
        const oauth = (op?.security ?? []).filter((req: object) => 'oauth2' in req)
        if (oauth.length > 1) repeated.push(`${method.toUpperCase()} ${path}`)
      }
    }
    expect(repeated).to.deep.equal([])
  })
})
