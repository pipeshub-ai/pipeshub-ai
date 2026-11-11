import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import yaml from 'js-yaml'
import { readFileSync } from 'fs'
import { join } from 'path'
import { ADMIN, MEMBER, MEMBER_ID, Harness, call, sessionToken, startHarness } from './connectors-http-harness'
import { Logger } from '../../../../src/libs/services/logger.service'

const CONNECTOR_ID = '3f2c9e7a-1b4d-4c8e-9a6f-2d5e8b7c1a90'
const RECORD_ID = 'rec-1'
const PATH = `/${CONNECTOR_ID}/permissions/explain`
const QUERY = `?userId=${MEMBER_ID}&recordId=${RECORD_ID}`
const BACKEND_PATH = `/api/v1/connectors/${CONNECTOR_ID}/permissions/explain`
const ANSWER = {
  admitted: false,
  code: 'restricted_container',
  container: { name: null, restricted: true },
  path: [{ name: null, restricted: true }],
  reference: 'abcd1234',
}

describe('Connector routes: why a person cannot read a record', () => {
  let h: Harness
  let info: sinon.SinonStub

  beforeEach(async () => {
    info = sinon.stub(Logger.prototype, 'info')
    h = await startHarness()
    h.backend.on('GET', BACKEND_PATH, { status: 200, body: ANSWER })
  })

  afterEach(async () => {
    sinon.restore()
    await h.close()
  })

  const audits = (): Array<Record<string, unknown>> =>
    info.getCalls().filter((c) => c.args[0] === 'Access explanation requested').map((c) => c.args[1] as Record<string, unknown>)

  it('answers an admin with what the connector service says, asking it with the admin’s own credentials', async () => {
    const token = sessionToken(h, ADMIN)

    const r = await call(h, 'GET', PATH + QUERY, token)

    expect(r.status).to.equal(200)
    expect(r.body).to.deep.equal(ANSWER)
    const forwarded = h.backend.callsTo('GET', BACKEND_PATH)
    expect(forwarded).to.have.length(1)
    expect(forwarded[0].query.get('user_id')).to.equal(MEMBER_ID)
    expect(forwarded[0].query.get('record_id')).to.equal(RECORD_ID)
    expect(forwarded[0].headers.authorization).to.equal(`Bearer ${token}`)
  })

  it('gives a member the 404 of a route that is not there, before anything reaches the connector service', async () => {
    const r = await call(h, 'GET', PATH + QUERY, sessionToken(h, MEMBER))
    const unknown = await call(h, 'GET', `/${CONNECTOR_ID}/permissions/nothing${QUERY}`, sessionToken(h, MEMBER))

    expect(r.status).to.equal(404)
    expect(h.backend.calls).to.have.length(0)
    expect(unknown.status).to.equal(404)
    expect(audits()).to.deep.equal([])
  })

  it('requires the person and the record', async () => {
    for (const query of ['', `?userId=${MEMBER_ID}`, `?recordId=${RECORD_ID}`]) {
      const r = await call(h, 'GET', PATH + query, sessionToken(h, ADMIN))
      expect(r.status, query).to.equal(400)
    }
    expect(h.backend.calls).to.have.length(0)
  })

  it('records who asked about whom and which record, with the reason code and no content', async () => {
    await call(h, 'GET', PATH + QUERY, sessionToken(h, ADMIN))

    expect(audits()).to.deep.equal([
      {
        actorUserId: ADMIN._id,
        orgId: ADMIN.orgId,
        connectorId: CONNECTOR_ID,
        targetUserId: MEMBER_ID,
        recordId: RECORD_ID,
        status: 200,
        code: 'restricted_container',
        admitted: false,
      },
    ])
  })

  it('records a check that failed, and passes the service’s 404 through', async () => {
    h.backend.on('GET', BACKEND_PATH, { status: 404, body: { detail: 'Not found' } })

    const r = await call(h, 'GET', PATH + QUERY, sessionToken(h, ADMIN))

    expect(r.status).to.equal(404)
    expect(audits()).to.have.length(1)
    expect(audits()[0]).to.include({ actorUserId: ADMIN._id, recordId: RECORD_ID, status: 404 })
  })

  it('limits an admin to 30 checks a minute and counts each admin apart', async () => {
    const token = sessionToken(h, ADMIN)
    for (let i = 0; i < 30; i += 1) {
      expect((await call(h, 'GET', PATH + QUERY, token)).status).to.equal(200)
    }

    const over = await call(h, 'GET', PATH + QUERY, token)

    expect(over.status).to.equal(429)
    expect(h.backend.callsTo('GET', BACKEND_PATH)).to.have.length(30)
  })
})

describe('The explanation route in the OpenAPI spec', () => {
  type Schema = { nullable?: boolean; allOf?: unknown; $ref?: string; properties?: Record<string, Schema>; items?: Schema }
  const spec = yaml.load(
    readFileSync(join(__dirname, '..', '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'), 'utf8'),
  ) as { components: { schemas: Record<string, Schema> }; paths: Record<string, any> }
  const answer = spec.paths['/connectors/{connectorId}/permissions/explain'].get.responses['200'].content['application/json']
    .schema as Schema

  it('makes the container nullable in a schema of its own, and a path item not nullable', () => {
    const container = answer.properties!.container
    expect(container.allOf).to.equal(undefined)
    expect(spec.components.schemas[container.$ref!.split('/').pop()!].nullable).to.equal(true)
    const item = spec.components.schemas[answer.properties!.path.items!.$ref!.split('/').pop()!]
    expect(item.nullable).to.not.equal(true)
  })
})
