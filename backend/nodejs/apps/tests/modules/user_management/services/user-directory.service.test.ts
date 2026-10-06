import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import {
  MongoUserDirectory,
  displayNameOf,
} from '../../../../src/modules/user_management/services/user-directory.service'

function stubFind(rows: Array<Record<string, unknown>>) {
  const chain = {
    select: sinon.stub().returnsThis(),
    lean: sinon.stub().returnsThis(),
    exec: sinon.stub().resolves(rows),
  }
  const find = sinon.stub(Users, 'find').returns(chain as never)
  return { find, chain }
}

describe('displayNameOf', () => {
  it('prefers fullName, then first+last, then email, else empty', () => {
    expect(displayNameOf({ fullName: ' Ada ', firstName: 'x' })).to.equal('Ada')
    expect(displayNameOf({ fullName: ' ', firstName: 'Grace', lastName: 'Hopper' })).to.equal('Grace Hopper')
    expect(displayNameOf({ firstName: ' ', email: ' a@b.c ' })).to.equal('a@b.c')
    expect(displayNameOf({})).to.equal('')
  })
})

describe('MongoUserDirectory', () => {
  afterEach(() => sinon.restore())

  const org = new mongoose.Types.ObjectId().toString()

  it('displayNames runs one org-scoped query for deduped valid ids and skips invalid ones', async () => {
    const a = new mongoose.Types.ObjectId()
    const { find, chain } = stubFind([{ _id: a, fullName: 'Ada' }])

    const names = await new MongoUserDirectory().displayNames(org, [a.toString(), a.toString(), 'bad'])

    expect(find.calledOnce).to.equal(true)
    const [filter] = find.firstCall.args as [{ orgId: mongoose.Types.ObjectId; isDeleted: boolean; _id: { $in: unknown[] } }]
    expect(filter.orgId.toString()).to.equal(org)
    expect(filter.isDeleted).to.equal(false)
    expect(filter._id.$in).to.have.length(1)
    expect(chain.select.calledOnceWithExactly('fullName firstName lastName email')).to.equal(true)
    expect([...names.entries()]).to.deep.equal([[a.toString(), 'Ada']])
  })

  it('displayNames gives an empty name to ids not found in the org (cross-org ids are filtered by the query)', async () => {
    const found = new mongoose.Types.ObjectId()
    const other = new mongoose.Types.ObjectId()
    stubFind([{ _id: found, email: 'f@x.y' }])

    const names = await new MongoUserDirectory().displayNames(org, [found.toString(), other.toString()])

    expect(names.get(found.toString())).to.equal('f@x.y')
    expect(names.get(other.toString())).to.equal('')
  })

  it('displayNames with emailFallback false never returns or selects the address', async () => {
    const noName = new mongoose.Types.ObjectId()
    const named = new mongoose.Types.ObjectId()
    const { chain } = stubFind([{ _id: noName, email: 'n@x.y' }, { _id: named, firstName: 'Lin', email: 'l@x.y' }])

    const names = await new MongoUserDirectory().displayNames(org, [noName.toString(), named.toString()], { emailFallback: false })

    expect(chain.select.calledOnceWithExactly('fullName firstName lastName')).to.equal(true)
    expect(names.get(noName.toString())).to.equal('')
    expect(names.get(named.toString())).to.equal('Lin')
  })

  it('displayNames does not query when there is no valid id', async () => {
    const { find } = stubFind([])
    expect((await new MongoUserDirectory().displayNames(org, ['x'])).size).to.equal(0)
    expect(find.called).to.equal(false)
  })

  it('findByIds maps kind and isDisabled, defaulting to an enabled human', async () => {
    const svc = new mongoose.Types.ObjectId()
    const human = new mongoose.Types.ObjectId()
    const { find } = stubFind([
      { _id: svc, fullName: 'Bot', kind: 'service', isDisabled: true },
      { _id: human, firstName: 'Lin', email: 'l@x.y' },
    ])

    const users = await new MongoUserDirectory().findByIds(org, [svc.toString(), human.toString(), svc.toString()])

    expect(find.calledOnce).to.equal(true)
    expect(users).to.deep.equal([
      { userId: svc.toString(), displayName: 'Bot', kind: 'service', isDisabled: true },
      { userId: human.toString(), displayName: 'Lin', email: 'l@x.y', kind: 'human', isDisabled: false },
    ])
  })

  it('findByIds returns nothing without querying when no id is valid', async () => {
    const { find } = stubFind([])
    expect(await new MongoUserDirectory().findByIds(org, [])).to.deep.equal([])
    expect(find.called).to.equal(false)
  })
})
