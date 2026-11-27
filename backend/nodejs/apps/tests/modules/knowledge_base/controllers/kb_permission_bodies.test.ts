import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  createKBPermission,
  removeKBPermission,
  updateKBPermission,
} from '../../../../src/modules/knowledge_base/controllers/kb_controllers'
import {
  deletePermissionsSchema,
  kbPermissionSchema,
  updatePermissionsSchema,
} from '../../../../src/modules/knowledge_base/validators/validators'
import * as connectorUtils from '../../../../src/modules/tokens_manager/utils/connector.utils'

/**
 * MCPFIX F8: the permission routes' schemas make `userIds` and `teamIds` each optional (one of them must
 * name someone), so a body the route admits may carry only one list. The controllers read both lists'
 * lengths and answered 500 for such a body.
 */
describe('KB permission routes: a body with one list absent (MCPFIX F8)', () => {
  const KB = '7f1d2c7e-6f0a-4f5e-9d0b-2b6a3c1e8a11'
  const appConfig = { connectorBackend: 'http://connectors' } as never
  let forwarded: sinon.SinonStub

  const request = (body: Record<string, unknown>): any => ({
    headers: { authorization: 'Bearer t' },
    params: { kbId: KB },
    body,
    query: {},
    user: { userId: 'user-1', orgId: 'org-1' },
    context: { requestId: 'req-1' },
  })
  const response = (): any => {
    const res: any = {}
    res.status = sinon.stub().returns(res)
    res.json = sinon.stub().returns(res)
    return res
  }
  const admitted = (schema: { safeParse: (value: unknown) => { success: boolean } }, body: unknown): boolean =>
    schema.safeParse({ body, params: { kbId: KB }, query: {}, headers: {} }).success

  beforeEach(() => {
    forwarded = sinon
      .stub(connectorUtils, 'executeConnectorCommand')
      .resolves({ statusCode: 200, data: { ok: true } } as never)
  })
  afterEach(() => sinon.restore())

  const CASES = [
    {
      name: 'create, users only',
      handler: createKBPermission,
      schema: kbPermissionSchema,
      body: { userIds: ['u-1'], role: 'READER' },
      sent: { userIds: ['u-1'], teamIds: [], role: 'READER' },
    },
    {
      name: 'create, teams only',
      handler: createKBPermission,
      schema: kbPermissionSchema,
      body: { teamIds: ['t-1'] },
      sent: { userIds: [], teamIds: ['t-1'] },
    },
    {
      name: 'update, users only',
      handler: updateKBPermission,
      schema: updatePermissionsSchema,
      body: { userIds: ['u-1'], role: 'WRITER' },
      sent: { userIds: ['u-1'], teamIds: [], role: 'WRITER' },
    },
    {
      name: 'remove, users only',
      handler: removeKBPermission,
      schema: deletePermissionsSchema,
      body: { userIds: ['u-1'] },
      sent: { userIds: ['u-1'], teamIds: [] },
    },
    {
      name: 'remove, teams only',
      handler: removeKBPermission,
      schema: deletePermissionsSchema,
      body: { teamIds: ['t-1'] },
      sent: { userIds: [], teamIds: ['t-1'] },
    },
  ]

  for (const { name, handler, schema, body, sent } of CASES) {
    it(`${name}: the body the schema admits reaches the connectors service with the absent list empty`, async () => {
      expect(admitted(schema, body), 'the route admits this body').to.equal(true)
      const res = response()
      const next = sinon.stub()
      await handler(appConfig)(request(body), res, next)
      expect(next.called, String(next.firstCall?.args[0]?.message)).to.equal(false)
      expect(forwarded.calledOnce).to.equal(true)
      expect(forwarded.firstCall.args[3]).to.deep.equal(sent)
      expect(res.json.calledOnce).to.equal(true)
    })
  }

  it('create with neither list is refused before anything is sent', async () => {
    expect(admitted(kbPermissionSchema, { role: 'READER' })).to.equal(false)
    const next = sinon.stub()
    await createKBPermission(appConfig)(request({ role: 'READER' }), response(), next)
    expect(next.calledOnce).to.equal(true)
    expect(next.firstCall.args[0].statusCode).to.equal(400)
    expect(forwarded.called).to.equal(false)
  })
})
