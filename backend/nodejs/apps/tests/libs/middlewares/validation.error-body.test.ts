import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import * as fs from 'fs'
import * as path from 'path'
import { ValidationMiddleware } from '../../../src/libs/middlewares/validation.middleware'
import { Logger } from '../../../src/libs/services/logger.service'
import { createPatTokenSchema } from '../../../src/modules/oauth_provider/validators/pat.validators'
import { authorizeQuerySchema } from '../../../src/modules/oauth_provider/validators/oauth.validators'
import {
  createProjectSchema,
  upsertProjectMembersSchema,
} from '../../../src/modules/projects/validators/project.validators'
import { restoreRecordsSchema } from '../../../src/modules/knowledge_base/validators/validators'
import { urlSchema } from '../../../src/modules/configuration_manager/validator/validators'
import { createTeamSchema } from '../../../src/modules/user_management/validators/teams.request.validators'

/**
 * Pins the client-visible ValidationError body (message + errors[]) produced
 * by ValidationMiddleware for real route schemas. The expected bodies live in
 * validation.error-body.fixture.json; they were recorded before the zod 4
 * migration and must not change without a deliberate API decision.
 * Re-record with UPDATE_VALIDATION_FIXTURE=1.
 */
const FIXTURE = path.join(__dirname, 'validation.error-body.fixture.json')
const OID = 'a'.repeat(24)

interface Case {
  name: string
  schema: any
  req: { body?: unknown; query?: unknown; params?: unknown }
  options?: { stripUnknown?: boolean }
}

const cases: Case[] = [
  { name: 'pat: missing required field', schema: createPatTokenSchema, req: { body: {} } },
  { name: 'pat: wrong type', schema: createPatTokenSchema, req: { body: { name: 123 } } },
  { name: 'pat: empty string / empty array', schema: createPatTokenSchema, req: { body: { name: '', scopes: [] } } },
  { name: 'pat: too long string', schema: createPatTokenSchema, req: { body: { name: 'x'.repeat(101) } } },
  { name: 'pat: bad union literal', schema: createPatTokenSchema, req: { body: { name: 'n', expiryDays: 7 } } },
  { name: 'pat: array element wrong type', schema: createPatTokenSchema, req: { body: { name: 'n', scopes: ['a', 5] } } },
  { name: 'pat: strict nested unknown key and bad uuid', schema: createPatTokenSchema, req: { body: { name: 'n', knowledgeLimit: { apps: ['nope'], bogus: 1 } } } },
  { name: 'pat: body missing entirely', schema: createPatTokenSchema, req: { body: undefined } },
  { name: 'project: bad enum / bad id / missing name', schema: upsertProjectMembersSchema, req: { params: { projectId: 'x' }, body: { members: [{ principalId: 'y', role: 'god' }] } } },
  { name: 'project: members empty', schema: upsertProjectMembersSchema, req: { params: { projectId: OID }, body: { members: [] } } },
  { name: 'project: create with custom min message', schema: createProjectSchema, req: { body: { name: '   ' } } },
  { name: 'project: create wrong types', schema: createProjectSchema, req: { body: { name: 5, description: {} } } },
  { name: 'oauth authorize: bad enum, bad url, missing', schema: authorizeQuerySchema, req: { query: { response_type: 'token', redirect_uri: 'not a url' } } },
  { name: 'config: bad url', schema: urlSchema, req: { body: { url: 'nope' } } },
  { name: 'restore: array bounds (too many)', schema: restoreRecordsSchema, req: { body: { recordIds: Array.from({ length: 101 }, () => 'r') } } },
  { name: 'restore: array bounds (empty)', schema: restoreRecordsSchema, req: { body: { recordIds: [] } } },
  { name: 'restore: not an array', schema: restoreRecordsSchema, req: { body: { recordIds: 'r' } } },
  { name: 'team: preprocess + trim/min', schema: createTeamSchema, req: { body: { name: '  ' } } },
  { name: 'team: missing body field', schema: createTeamSchema, req: { body: {} } },
  { name: 'stripUnknown option, wrong type', schema: createPatTokenSchema, req: { body: { name: 1 } }, options: { stripUnknown: true } },
]

async function run(c: Case) {
  const req: any = { headers: {}, body: {}, query: {}, params: {}, path: '/t', method: 'POST', ...c.req }
  const next = sinon.stub()
  await ValidationMiddleware.validate(c.schema, c.options)(req, {} as any, next)
  expect(next.calledOnce, c.name).to.equal(true)
  const err = next.firstCall.args[0]
  expect(err, c.name).to.be.an('error')
  const j = err.toJSON()
  return {
    code: j.code,
    statusCode: j.statusCode,
    message: j.message,
    errors: j.errors,
  }
}

describe('ValidationMiddleware error body (client-visible contract)', () => {
  beforeEach(() => {
    const l = Logger.getInstance()
    sinon.stub(l, 'error')
    sinon.stub(l, 'debug')
  })
  afterEach(() => sinon.restore())

  const recorded: Record<string, unknown> = {}
  const expected: Record<string, unknown> =
    fs.existsSync(FIXTURE) && !process.env.UPDATE_VALIDATION_FIXTURE
      ? JSON.parse(fs.readFileSync(FIXTURE, 'utf8'))
      : {}

  for (const c of cases) {
    it(c.name, async () => {
      const body = JSON.parse(JSON.stringify(await run(c)))
      if (process.env.UPDATE_VALIDATION_FIXTURE) {
        recorded[c.name] = body
        const all = { ...(fs.existsSync(FIXTURE) ? JSON.parse(fs.readFileSync(FIXTURE, 'utf8')) : {}), ...recorded }
        fs.writeFileSync(FIXTURE, JSON.stringify(all, null, 2) + '\n')
        return
      }
      expect(expected[c.name], 'missing fixture entry').to.not.equal(undefined)
      expect(body).to.deep.equal(expected[c.name])
    })
  }
})
