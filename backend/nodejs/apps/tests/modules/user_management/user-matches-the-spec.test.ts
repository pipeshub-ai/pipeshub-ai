import 'reflect-metadata'
import { expect } from 'chai'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'
import { Users } from '../../../src/modules/user_management/schema/users.schema'

/**
 * What a user endpoint sends must be what the published spec promises.
 *
 * `User` and `UpdateUserResponse` both set `additionalProperties: false`, and
 * both list every stored field by hand. A field added to the Mongoose schema
 * without being added to both makes every user response-validation test fail
 * at once - which is what `kind` and `isDisabled` did, and only on the
 * nightly. This checks it on every run.
 */

const SPEC = join(
  __dirname, '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml',
)

interface SpecSchema {
  additionalProperties?: boolean
  properties?: Record<string, unknown>
}

function schemas(): Record<string, SpecSchema> {
  const spec = yaml.load(readFileSync(SPEC, 'utf8')) as {
    components: { schemas: Record<string, SpecSchema> }
  }
  return spec.components.schemas
}

/**
 * Every closed schema in the spec that describes a stored user, found rather
 * than listed.
 *
 * `refreshToken` answers with `RefreshTokenUser`, its own copy rather than a
 * reference to `User`, and it is just as closed - so a field missing there
 * fails just as hard, and it was missed once because a hand-kept list did not
 * mention it. A fourth copy would be missed the same way, so nothing is
 * hand-kept: a closed object carrying `_id`, `orgId` and `email` is a user.
 */
function userSchemaNames(): string[] {
  return Object.entries(schemas())
    .filter(([, schema]) => {
      if (schema.additionalProperties !== false) return false
      const props = Object.keys(schema.properties ?? {})
      return ['_id', 'orgId', 'email'].every((key) => props.includes(key))
    })
    .map(([name]) => name)
}

function declaredProperties(name: string): string[] {
  const schema = schemas()[name]
  if (!schema) throw new Error(`the spec no longer declares a ${name} schema`)
  return Object.keys(schema.properties ?? {})
}

/**
 * The stored field names, one level deep: Mongoose reports a subdocument as
 * `address.city`, but the spec declares `address` once and refs its own
 * schema, so comparing full paths would report a gap that is not there.
 */
function storedFields(): string[] {
  return [
    ...new Set(
      // `?? path` only to satisfy the compiler: split always yields a first
      // element, but the index signature is typed as possibly undefined.
      Object.keys(Users.schema.paths).map((path) => path.split('.')[0] ?? path),
    ),
  ]
}

describe('the user document matches the published spec', () => {
  it('stores no field the spec has not been told about', () => {
    const stored = storedFields()
    expect(stored, 'the user schema should still have fields').to.not.be.empty

    const missing = userSchemaNames().flatMap((name) => {
      const declared = declaredProperties(name)
      return stored.filter((field) => !declared.includes(field)).map((field) => `${name}: ${field}`)
    })

    expect(
      missing,
      'add each one to that schema in modules/api-docs/pipeshub-openapi.yaml before shipping it',
    ).to.deep.equal([])
  })

  it('finds every closed copy of the user, including ones nobody listed', () => {
    // If this shrinks, a copy stopped being recognized and stopped being
    // guarded; the field that drifts next will be in the one that got away.
    expect(userSchemaNames().sort())
      .to.deep.equal(['RefreshTokenUser', 'UpdateUserResponse', 'User'])
  })

  it('describes the same user in every schema', () => {
    const user = declaredProperties('User')

    // UpdateUserResponse is User plus exactly `meta`, and every other copy is
    // User exactly. Asserting the extras rather than only the omissions is what
    // makes this catch a schema that drops `meta` as well as one that forgets a
    // field, and the whole point is that hand-kept copies drift.
    const extras: Record<string, string[]> = { UpdateUserResponse: ['meta'] }

    for (const name of userSchemaNames().filter((n) => n !== 'User')) {
      const other = declaredProperties(name)
      expect({
        [`${name} is missing`]: user.filter((field) => !other.includes(field)),
        [`${name} adds`]: other.filter((field) => !user.includes(field)),
      }).to.deep.equal({
        [`${name} is missing`]: [],
        [`${name} adds`]: extras[name] ?? [],
      })
    }
  })
})
