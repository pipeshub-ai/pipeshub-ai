import { expect } from 'chai'
import mongoose from 'mongoose'
import sinon from 'sinon'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import {
  ACCESS_REQUESTS_PER_HOUR,
  createAccessRequest,
  decideAccessRequest,
} from '../../../../src/modules/enterprise_search/controller/access_request.controller'
import { AccessRequest } from '../../../../src/modules/enterprise_search/schema/access.request.schema'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema'

const oid = () => new mongoose.Types.ObjectId()
const ORG = String(oid())
const VIEWER = String(oid())
const OWNER = String(oid())
const config: any = { jwtSecret: 's', scopedJwtSecret: 's2', aiBackend: 'http://ai' }

const run = async (handler: any, req: any) => {
  const out: any = {}
  const res: any = {
    status: (c: number) => ((out.status = c), res),
    json: (b: any) => ((out.body = b), res),
  }
  await handler({ user: { userId: VIEWER, orgId: ORG }, body: {}, params: {}, ...req }, res, (e: any) => (out.error = e))
  return out
}

describe('access requests', () => {
  let python: sinon.SinonStub
  let notify: sinon.SinonStub
  beforeEach(() => {
    python = sinon.stub(AIServiceCommand.prototype, 'execute')
    notify = sinon.stub(Notifications, 'create').resolves({ toObject: () => ({}) } as any)
    sinon.stub(AccessRequest, 'findOne').returns({ lean: async () => null } as any)
    sinon.stub(AccessRequest, 'countDocuments').resolves(0 as any)
    sinon.stub(Users, 'findById').returns({ select: () => ({ lean: async () => ({ fullName: 'Casey Viewer' }) }) } as any)
  })
  afterEach(() => sinon.restore())

  const denied = (detail: any) =>
    python.resolves({ statusCode: 200, data: { accessible: [], deniedDetails: [detail] } } as any)

  it('T4: a native record request is stored pending for the owner and notifies them', async () => {
    denied({ recordId: 'kb1', kind: 'native', recordName: 'Plan', collection: { id: 'col-1', name: 'Finance' }, ownerUserIds: [OWNER] })
    const create = sinon.stub(AccessRequest, 'create').callsFake(async (d: any) => ({ _id: oid(), ...d }) as any)
    const out = await run(createAccessRequest(config), { body: { recordId: 'kb1' } })
    expect(out.status).to.equal(201)
    // Raw viewer response: no collection or record name, no owners.
    const raw = JSON.stringify(out.body)
    for (const leak of ['Finance', 'Plan', OWNER, 'ownerUserIds', 'collectionName', 'recordName']) expect(raw).to.not.include(leak)
    expect(create.firstCall.args[0]).to.include({ recordId: 'kb1', kind: 'native', status: 'pending', collectionId: 'col-1', collectionName: 'Finance' })
    expect((create.firstCall.args[0] as any).ownerUserIds.map(String)).to.deep.equal([OWNER])
    expect(String(notify.firstCall.args[0].assignedTo)).to.equal(OWNER)
    expect(notify.firstCall.args[0].message).to.equal('Casey Viewer asked for read access to the collection "Finance".')
    expect(notify.firstCall.args[0].payload.collectionName).to.equal('Finance')
    expect(out.body.sourceUrl).to.equal(undefined)
  })

  it('T4: the owner approves, Python grants, and the requester is told', async () => {
    const doc: any = {
      _id: oid(), kind: 'native', status: 'pending', recordId: 'kb1', recordName: 'Plan',
      ownerUserIds: [new mongoose.Types.ObjectId(OWNER)], requesterId: new mongoose.Types.ObjectId(VIEWER),
      save: sinon.stub().resolves(),
    }
    ;(AccessRequest.findOne as sinon.SinonStub).resolves(doc)
    python.onFirstCall().resolves({ statusCode: 200, data: { granted: 1 } } as any)
    python.onSecondCall().resolves({ statusCode: 200, data: { accessible: ['kb1'] } } as any)
    const out = await run(decideAccessRequest(config, 'approve'), {
      user: { userId: OWNER, orgId: ORG }, params: { requestId: String(doc._id) },
    })
    expect(out.status).to.equal(200)
    expect(doc.status).to.equal('approved')
    expect(String(notify.firstCall.args[0].assignedTo)).to.equal(VIEWER)
  })

  it('does not mark approved when the grant did not take effect', async () => {
    const doc: any = {
      _id: oid(), kind: 'native', status: 'pending', recordId: 'kb1',
      ownerUserIds: [new mongoose.Types.ObjectId(OWNER)], requesterId: new mongoose.Types.ObjectId(VIEWER),
      save: sinon.stub().resolves(),
    }
    ;(AccessRequest.findOne as sinon.SinonStub).resolves(doc)
    python.onFirstCall().resolves({ statusCode: 200, data: { granted: 0 } } as any)
    python.onSecondCall().resolves({ statusCode: 200, data: { accessible: [] } } as any)
    const out = await run(decideAccessRequest(config, 'approve'), {
      user: { userId: OWNER, orgId: ORG }, params: { requestId: String(doc._id) },
    })
    expect(out.error?.statusCode).to.equal(409)
    expect(doc.save.called).to.equal(false)
  })

  it('only the owner may decide', async () => {
    const doc: any = { kind: 'native', status: 'pending', ownerUserIds: [new mongoose.Types.ObjectId(OWNER)], save: sinon.stub() }
    ;(AccessRequest.findOne as sinon.SinonStub).resolves(doc)
    const out = await run(decideAccessRequest(config, 'approve'), { params: { requestId: String(oid()) } })
    expect(out.error?.statusCode).to.equal(403)
    expect(python.called).to.equal(false)
  })

  it('T5: a connector record is forwarded to the source and never granted locally', async () => {
    denied({ recordId: 'wiki', kind: 'connector', recordName: 'Wiki', webUrl: 'https://wiki.example/p/1', ownerUserIds: [OWNER] })
    const create = sinon.stub(AccessRequest, 'create').callsFake(async (d: any) => ({ _id: oid(), ...d }) as any)
    const out = await run(createAccessRequest(config), { body: { recordId: 'wiki' } })
    expect(out.status).to.equal(201)
    expect(out.body.sourceUrl).to.equal('https://wiki.example/p/1')
    expect((create.firstCall.args[0] as any).status).to.equal('forwarded')
    // The only Python call is the access check, never access-grant.
    expect(python.callCount).to.equal(1)
  })

  it('an approve on a connector request is refused', async () => {
    const doc: any = { kind: 'connector', status: 'forwarded', ownerUserIds: [new mongoose.Types.ObjectId(OWNER)] }
    ;(AccessRequest.findOne as sinon.SinonStub).resolves(doc)
    const out = await run(decideAccessRequest(config, 'approve'), {
      user: { userId: OWNER, orgId: ORG }, params: { requestId: String(oid()) },
    })
    expect(out.error?.statusCode).to.equal(400)
    expect(python.called).to.equal(false)
  })

  it('is rate limited per user', async () => {
    ;(AccessRequest.countDocuments as sinon.SinonStub).resolves(ACCESS_REQUESTS_PER_HOUR)
    denied({ recordId: 'kb1', kind: 'native', collection: null, ownerUserIds: [OWNER] })
    const out = await run(createAccessRequest(config), { body: { recordId: 'kb1' } })
    expect(out.error?.statusCode).to.equal(429)
    expect(notify.called).to.equal(false)
  })

  it('does not create a request for a record the viewer can already read', async () => {
    python.resolves({ statusCode: 200, data: { accessible: ['kb1'] } } as any)
    expect((await run(createAccessRequest(config), { body: { recordId: 'kb1' } })).body).to.deep.equal({ alreadyAccessible: true })
  })

  it('answers an unknown, hidden or unapprovable record exactly like an accepted request, storing and notifying nothing', async () => {
    const create = sinon.stub(AccessRequest, 'create').callsFake(async (d: any) => ({ _id: oid(), ...d }) as any)
    denied({ recordId: 'real', kind: 'native', collection: { id: 'col-1', name: 'Finance' }, ownerUserIds: [OWNER] })
    const real = await run(createAccessRequest(config), { body: { recordId: 'real' } })
    create.resetHistory()
    notify.resetHistory()

    const answers = []
    denied({ recordId: 'x', kind: 'unknown' })
    answers.push(await run(createAccessRequest(config), { body: { recordId: 'x' } }))
    python.resolves({ statusCode: 200, data: { accessible: [], deniedDetails: [] } } as any)
    answers.push(await run(createAccessRequest(config), { body: { recordId: 'y' } }))
    denied({ recordId: 'kb2', kind: 'native', collection: null, ownerUserIds: [] })
    answers.push(await run(createAccessRequest(config), { body: { recordId: 'kb2' } }))

    for (const out of answers) {
      expect(out.error).to.equal(undefined)
      expect(out.status).to.equal(real.status)
      expect(Object.keys(out.body)).to.deep.equal(Object.keys(real.body))
      expect(Object.keys(out.body.request)).to.deep.equal(Object.keys(real.body.request))
      expect(out.body.request).to.include({ kind: 'native', status: 'pending' })
    }
    expect(create.called).to.equal(false)
    expect(notify.called).to.equal(false)
  })

  it('does not repeat a pending request', async () => {
    ;(AccessRequest.findOne as sinon.SinonStub).returns({ lean: async () => ({ _id: oid(), recordId: 'kb1', kind: 'native', status: 'pending', requesterId: VIEWER }) })
    denied({ recordId: 'kb1', kind: 'native', collection: { id: 'col-1', name: 'Finance' }, ownerUserIds: [OWNER] })
    const out = await run(createAccessRequest(config), { body: { recordId: 'kb1' } })
    expect(out.body.duplicate).to.equal(true)
    // One open request per collection, whichever record of it was asked for.
    expect((AccessRequest.findOne as sinon.SinonStub).firstCall.args[0]).to.include({ collectionId: 'col-1' })
    expect(notify.called).to.equal(false)
  })

  it('notifies every owner of the collection', async () => {
    const other = String(oid())
    denied({ recordId: 'kb1', kind: 'native', collection: { id: 'col-1', name: 'Finance' }, ownerUserIds: [OWNER, other] })
    sinon.stub(AccessRequest, 'create').callsFake(async (d: any) => ({ _id: oid(), ...d }) as any)
    await run(createAccessRequest(config), { body: { recordId: 'kb1' } })
    expect(notify.getCalls().map((c) => String(c.args[0].assignedTo))).to.have.members([OWNER, other])

  })

  it('any collection owner may read the request state; others get 404', async () => {
    const doc = { _id: oid(), kind: 'native', status: 'approved', recordId: 'kb1', collectionName: 'Finance', requesterId: VIEWER }
    ;(AccessRequest.findOne as sinon.SinonStub).returns({ lean: async () => doc })
    const { getAccessRequest } = await import('../../../../src/modules/enterprise_search/controller/access_request.controller')
    const out = await run(getAccessRequest, { user: { userId: OWNER, orgId: ORG }, params: { requestId: String(doc._id) } })
    expect(out.body.request.status).to.equal('approved')
    expect((AccessRequest.findOne as sinon.SinonStub).firstCall.args[0].ownerUserIds.toString()).to.equal(OWNER)
    ;(AccessRequest.findOne as sinon.SinonStub).returns({ lean: async () => null })
    expect((await run(getAccessRequest, { params: { requestId: String(oid()) } })).error?.statusCode).to.equal(404)
  })
})
