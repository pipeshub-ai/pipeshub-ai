import { expect } from 'chai'
import mongoose from 'mongoose'
import sinon from 'sinon'
import { AIServiceCommand } from '../../../../src/libs/commands/ai_service/ai.service.command'
import {
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
    // The decision is claimed on the stored row: a pending row becomes the decided one.
    sinon.stub(AccessRequest, 'findOneAndUpdate').callsFake(((_filter: any, update: any) => ({
      lean: async () => ({ _id: oid(), kind: 'native', requesterId: VIEWER, recordId: 'kb1', ...update.$set }),
    })) as any)
    sinon.stub(AccessRequest, 'updateOne').resolves({} as any)
  })
  afterEach(() => sinon.restore())

  it('the owner approves, Python grants, and the requester is told', async () => {
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
    expect(out.body.request.status).to.equal('approved')
    const [filter, update] = (AccessRequest.findOneAndUpdate as sinon.SinonStub).firstCall.args
    expect(filter.status).to.equal('pending')
    expect(update.$set.status).to.equal('approved')
    // Once granted, the request is no longer open.
    expect((AccessRequest.updateOne as sinon.SinonStub).lastCall.args[1]).to.deep.equal({ $unset: { openKey: 1 } })
    expect(String(notify.firstCall.args[0].assignedTo)).to.equal(VIEWER)
  })

  it('a request another owner already decided gets 409, and nothing is granted or sent', async () => {
    const doc: any = {
      _id: oid(), kind: 'native', status: 'pending', recordId: 'kb1',
      ownerUserIds: [new mongoose.Types.ObjectId(OWNER)], requesterId: new mongoose.Types.ObjectId(VIEWER),
    }
    ;(AccessRequest.findOne as sinon.SinonStub).resolves(doc)
    ;(AccessRequest.findOneAndUpdate as sinon.SinonStub).returns({ lean: async () => null } as any)
    const out = await run(decideAccessRequest(config, 'approve'), {
      user: { userId: OWNER, orgId: ORG }, params: { requestId: String(doc._id) },
    })
    expect(out.error?.statusCode).to.equal(409)
    expect(python.called).to.equal(false)
    expect(notify.called).to.equal(false)
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
    // The claim is released, so the request is pending again and can be decided later.
    const [filter, update] = (AccessRequest.updateOne as sinon.SinonStub).lastCall.args
    expect(filter.status).to.equal('approved')
    expect(update.$set.status).to.equal('pending')
    expect(notify.called).to.equal(false)
  })

  it('only the owner may decide', async () => {
    const doc: any = { kind: 'native', status: 'pending', ownerUserIds: [new mongoose.Types.ObjectId(OWNER)], save: sinon.stub() }
    ;(AccessRequest.findOne as sinon.SinonStub).resolves(doc)
    const out = await run(decideAccessRequest(config, 'approve'), { params: { requestId: String(oid()) } })
    expect(out.error?.statusCode).to.equal(403)
    expect(python.called).to.equal(false)
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
