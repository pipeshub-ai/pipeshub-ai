import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'
import { Users } from '../../../../src/modules/user_management/schema/users.schema'
import {
  MongoUserDirectory,
  SEARCH_RANK,
  displayNameOf,
  matchesSearchTokens,
  searchRank,
  searchTokens,
} from '../../../../src/modules/user_management/services/user-directory.service'

function stubFind(rows: Array<Record<string, unknown>>) {
  const chain = {
    select: sinon.stub().returnsThis(),
    sort: sinon.stub().returnsThis(),
    limit: sinon.stub().returnsThis(),
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
      { userId: svc.toString(), displayName: 'Bot', fullName: 'Bot', kind: 'service', isDisabled: true },
      { userId: human.toString(), displayName: 'Lin', email: 'l@x.y', firstName: 'Lin', kind: 'human', isDisabled: false },
    ])
  })

  it('findByIds returns nothing without querying when no id is valid', async () => {
    const { find } = stubFind([])
    expect(await new MongoUserDirectory().findByIds(org, [])).to.deep.equal([])
    expect(find.called).to.equal(false)
  })
})

describe('user search tokens', () => {
  it('splits on whitespace, lowercases, and caps the number and length of tokens', () => {
    expect(searchTokens('  Jo   SM ')).to.deep.equal(['jo', 'sm'])
    expect(searchTokens('')).to.deep.equal([])
    expect(searchTokens('a b c d e f g')).to.have.length(5)
    expect(searchTokens('x'.repeat(500))[0]).to.have.length(64)
  })

  const john = { fullName: 'John Michael Smith', firstName: 'John', middleName: 'Michael', lastName: 'Smith', email: 'js@acme.test' }
  it('every token must prefix a first, middle or last name, a word of the full name or the email', () => {
    for (const q of ['john', 'mich', 'smi', 'js@', 'jo sm', 'mich john', 'michael smith']) expect(matchesSearchTokens(john, searchTokens(q)), q).to.equal(true)
    for (const q of ['ohn', 'smith2', 'jo zz', 'acme']) expect(matchesSearchTokens(john, searchTokens(q)), q).to.equal(false)
  })

  it('a word of the full name matches when no name part is stored', () => {
    expect(matchesSearchTokens({ fullName: 'Mary Jane Watson' }, ['jane'])).to.equal(true)
  })
})

describe('MongoUserDirectory.searchOrgMembers', () => {
  afterEach(() => sinon.restore())
  const org = new mongoose.Types.ObjectId().toString()

  it('queries only active humans of the org, one anchored escaped regex group per token, with a cap', async () => {
    const id = new mongoose.Types.ObjectId()
    const { find, chain } = stubFind([])
    chain.exec.onFirstCall().resolves([{ _id: id, firstName: 'John', lastName: 'Smith', email: 'j@x.y' }])

    const out = await new MongoUserDirectory().searchOrgMembers(org, 'jo sm.*', 7)

    const [filter] = find.firstCall.args as [Record<string, any>]
    expect(filter.orgId.toString()).to.equal(org)
    expect(filter.isDeleted).to.equal(false)
    expect(filter.isDisabled).to.deep.equal({ $ne: true })
    expect(filter.kind).to.deep.equal({ $in: ['human', null] })
    expect(filter.$and).to.have.length(2)
    const second = filter.$and[1].$or as Array<Record<string, RegExp>>
    const regexes = second.map((c) => Object.values(c)[0] as RegExp)
    expect(regexes.map((r) => r.source)).to.deep.equal(['^sm\\.\\*', '^sm\\.\\*', '^sm\\.\\*', '^sm\\.\\*', '(^|\\s)sm\\.\\*'])
    expect(regexes.every((r) => r.flags === 'i')).to.equal(true)
    expect(chain.select.firstCall.args[0]).to.equal('fullName firstName middleName lastName email kind isDisabled')
    expect(chain.limit.firstCall.args).to.deep.equal([7])
    expect(out).to.deep.equal([{ userId: id.toString(), displayName: 'John Smith', email: 'j@x.y', firstName: 'John', lastName: 'Smith', kind: 'human', isDisabled: false }])
  })

  it('fills the cap tier by tier: first name, then another name word, then the rest, never repeating a person', async () => {
    const id = new mongoose.Types.ObjectId()
    const { find, chain } = stubFind([])
    chain.exec.onFirstCall().resolves([{ _id: id, firstName: 'Ann' }])

    await new MongoUserDirectory().searchOrgMembers(org, 'an', 5)

    expect(find.callCount).to.equal(3)
    const filters = find.getCalls().map((c) => c.args[0] as Record<string, any>)
    const sources = (f: Record<string, any>) => (f.$or as Array<Record<string, RegExp>>).map((c) => `${Object.keys(c)[0]}:${Object.values(c)[0]!.source}`)
    expect(sources(filters[0]!)).to.deep.equal(['firstName:^an', 'fullName:^an'])
    expect(filters[0]!._id).to.equal(undefined)
    expect(sources(filters[1]!)).to.deep.equal(['middleName:^an', 'lastName:^an', 'fullName:\\san'])
    expect(filters[2]!.$or).to.equal(undefined)
    for (const f of filters.slice(1)) expect(f._id.$nin.map(String)).to.deep.equal([id.toString()])
    expect(chain.limit.getCalls().map((c) => c.args[0])).to.deep.equal([5, 4, 4])
  })

  it('stops at the first tier that fills the cap', async () => {
    const { find, chain } = stubFind([])
    chain.exec.onFirstCall().resolves([
      { _id: new mongoose.Types.ObjectId(), firstName: 'Ann' },
      { _id: new mongoose.Types.ObjectId(), firstName: 'Anna' },
    ])
    await new MongoUserDirectory().searchOrgMembers(org, 'an', 2)
    expect(find.callCount).to.equal(1)
  })

  it('does not query for an empty query, a zero limit or a bad org id', async () => {
    const { find } = stubFind([])
    const dir = new MongoUserDirectory()
    expect(await dir.searchOrgMembers(org, '   ', 5)).to.deep.equal([])
    expect(await dir.searchOrgMembers(org, 'jo', 0)).to.deep.equal([])
    expect(await dir.searchOrgMembers('nope', 'jo', 5)).to.deep.equal([])
    expect(find.called).to.equal(false)
  })
})

describe('searchRank', () => {
  it('ranks an exact first name, then a first-name prefix, then another name word, then the rest', () => {
    expect(searchRank({ firstName: 'Al', fullName: 'Al Bundy' }, ['al'])).to.equal(SEARCH_RANK.exactFirstName)
    expect(searchRank({ fullName: 'Al Bundy' }, ['al'])).to.equal(SEARCH_RANK.exactFirstName)
    expect(searchRank({ firstName: 'Alan', fullName: 'Alan Turing' }, ['al'])).to.equal(SEARCH_RANK.firstName)
    expect(searchRank({ firstName: 'Zed', lastName: 'Alder', fullName: 'Zed Alder' }, ['al'])).to.equal(SEARCH_RANK.nameWord)
    expect(searchRank({ firstName: 'Zed', fullName: 'Zed Young', email: 'al@x.y' }, ['al'])).to.equal(SEARCH_RANK.other)
    expect(searchRank({ firstName: 'Al' }, [])).to.equal(SEARCH_RANK.other)
  })
})

type Row = Record<string, any>

/** Users.find over rows held in memory, for the operators searchOrgMembers sends. */
function fakeUsers(rows: Row[]) {
  const matches = (row: Row, filter: Row): boolean =>
    Object.entries(filter).every(([key, cond]) => {
      if (key === '$and') return (cond as Row[]).every((f) => matches(row, f))
      if (key === '$or') return (cond as Row[]).some((f) => matches(row, f))
      const value = row[key]
      if (cond instanceof RegExp) return typeof value === 'string' && cond.test(value)
      if (cond instanceof mongoose.Types.ObjectId) return String(value) === String(cond)
      if (cond !== null && typeof cond === 'object') {
        if ('$ne' in cond) return value !== cond.$ne
        if ('$in' in cond) return (cond.$in as unknown[]).some((v) => (v === null ? value === undefined || value === null : String(v) === String(value)))
        if ('$nin' in cond) return !(cond.$nin as unknown[]).some((v) => String(v) === String(value))
      }
      return value === cond
    })
  const byName = (a: Row, b: Row): number => (a.fullName < b.fullName ? -1 : a.fullName > b.fullName ? 1 : String(a._id) < String(b._id) ? -1 : 1)
  sinon.stub(Users, 'find').callsFake(((filter: Row) => {
    let limit = Infinity
    const chain = {
      select: () => chain,
      sort: () => chain,
      limit: (n: number) => {
        limit = n
        return chain
      },
      lean: () => chain,
      exec: async () => rows.filter((r) => matches(r, filter)).sort(byName).slice(0, limit),
    }
    return chain
  }) as never)
}

describe('MongoUserDirectory.searchOrgMembers ranking', () => {
  afterEach(() => sinon.restore())
  const org = new mongoose.Types.ObjectId()
  const person = (firstName: string, lastName: string, extra: Row = {}): Row => ({
    _id: new mongoose.Types.ObjectId(),
    orgId: org,
    isDeleted: false,
    firstName,
    lastName,
    fullName: `${firstName} ${lastName}`,
    email: `${firstName.toLowerCase()}.${lastName.toLowerCase()}@acme.test`,
    ...extra,
  })
  const names = async (q: string, limit = 20) => (await new MongoUserDirectory().searchOrgMembers(org.toString(), q, limit)).map((u) => u.displayName)

  it('puts first-name matches ahead of 30 people who sort first and match only by last name', async () => {
    const lastNameOnly = Array.from({ length: 30 }, (_, i) => person(`Aa${String(i).padStart(2, '0')}`, 'Abbott'))
    fakeUsers([...lastNameOnly, person('Abhishek', 'Rao'), person('Ab', 'Zed')])
    const out = await names('ab')
    expect(out).to.have.length(20)
    expect(out.slice(0, 2)).to.deep.equal(['Ab Zed', 'Abhishek Rao'])
    expect(out.slice(2).every((n) => n.endsWith('Abbott'))).to.equal(true)
  })

  it('ranks another name word ahead of an email-only match, then alphabetically', async () => {
    fakeUsers([
      person('Bea', 'Young', { email: 'mo@acme.test' }),
      person('Carl', 'Moss'),
      person('Mona', 'Lisa'),
      person('Al', 'Gore', { fullName: 'Al Mo Gore' }),
    ])
    expect(await names('mo')).to.deep.equal(['Mona Lisa', 'Al Mo Gore', 'Carl Moss', 'Bea Young'])
  })

  it('still leaves out other orgs, the deleted, the disabled and service users', async () => {
    fakeUsers([
      person('Ab', 'Here'),
      person('Ab', 'Gone', { isDeleted: true }),
      person('Ab', 'Off', { isDisabled: true }),
      person('Ab', 'Bot', { kind: 'service' }),
      person('Ab', 'Away', { orgId: new mongoose.Types.ObjectId() }),
    ])
    expect(await names('ab')).to.deep.equal(['Ab Here'])
  })
})
