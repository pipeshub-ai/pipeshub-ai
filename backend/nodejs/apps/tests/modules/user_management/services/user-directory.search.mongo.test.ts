import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import { MongoUserDirectory } from '../../../../src/modules/user_management/services/user-directory.service'

const uri = process.env.PCC_MONGO_URI

;(uri ? describe : describe.skip)('searchOrgMembers against a real MongoDB', function () {
  this.timeout(60_000)
  const org = new mongoose.Types.ObjectId()
  const other = new mongoose.Types.ObjectId()
  const make = (over: Record<string, unknown>) =>
    Users.create({ orgId: org, email: `${new mongoose.Types.ObjectId()}@acme.test`, fullName: 'x', hasLoggedIn: true, ...over } as never)

  before(async () => {
    await mongoose.connect(uri as string)
    await make({ fullName: 'John Michael Smith', firstName: 'John', middleName: 'Michael', lastName: 'Smith', email: 'js@acme.test' })
    await make({ fullName: 'Joanna Smythe', firstName: 'Joanna', lastName: 'Smythe' })
    await make({ fullName: 'John Gone', firstName: 'John', lastName: 'Gone', isDeleted: true })
    await make({ fullName: 'John Off', firstName: 'John', lastName: 'Off', isDisabled: true })
    await make({ fullName: 'John Robot', firstName: 'John', lastName: 'Robot', kind: 'service', email: 'john@svc.pipeshub.invalid' })
    await make({ fullName: 'John Foreign', firstName: 'John', lastName: 'Foreign', orgId: other })
  })
  after(async () => {
    await Users.deleteMany({ orgId: { $in: [org, other] } })
    await mongoose.disconnect()
  })

  const find = async (q: string, limit = 10) => (await new MongoUserDirectory().searchOrgMembers(String(org), q, limit)).map((u) => u.displayName)

  it('matches first, middle, last name and email, every token, case-insensitively', async () => {
    for (const q of ['john', 'MICH', 'smi', 'js@acme', 'jo smy', 'michael smith']) expect(await find(q), q).to.not.deep.equal([])
    expect(await find('jo sm')).to.have.members(['John Michael Smith', 'Joanna Smythe'])
    expect(await find('mich smy')).to.deep.equal([])
  })

  it('never returns deleted, disabled, service or other-org users', async () => {
    expect(await find('john')).to.deep.equal(['John Michael Smith'])
  })

  it('treats the query as text, never a pattern', async () => {
    expect(await find('.*')).to.deep.equal([])
    expect(await find('(a+)+$')).to.deep.equal([])
    expect(await find('j[')).to.deep.equal([])
  })

  it('caps the result', async () => {
    expect(await find('jo', 1)).to.have.length(1)
  })
})
