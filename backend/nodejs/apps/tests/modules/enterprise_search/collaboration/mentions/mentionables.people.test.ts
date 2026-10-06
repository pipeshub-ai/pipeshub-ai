import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Types } from 'mongoose'
import { Fixture, OTHER_ORG, PEOPLE, asUser, id, startFixture, user } from '../../helpers/collab-fixture'

const newId = (): string => String(new Types.ObjectId())
const JOHN = newId()
const JOANNA = newId()
const NAMELESS = newId()
const OFF = newId()
const BOT = newId()
const FOREIGN = newId()

const users = {
  [id('A')]: { displayName: 'Alice Anderson', firstName: 'Alice', lastName: 'Anderson', email: 'alice@acme.test' },
  [id('B')]: { displayName: 'Bob Brown', firstName: 'Bob', lastName: 'Brown', email: 'bob@acme.test' },
  [JOHN]: { displayName: 'John Smith', firstName: 'John', middleName: 'Michael', lastName: 'Smith', email: 'john.smith@acme.test' },
  [JOANNA]: { displayName: 'Joanna Smythe', firstName: 'Joanna', lastName: 'Smythe', email: 'joanna@acme.test' },
  [NAMELESS]: { displayName: '', email: 'zed@acme.test' },
  [OFF]: { displayName: 'John Disabled', firstName: 'John', lastName: 'Disabled', isDisabled: true },
  [BOT]: { displayName: 'John Robot', firstName: 'John', lastName: 'Robot', kind: 'service' as const },
  [FOREIGN]: { displayName: 'John Foreign', firstName: 'John', lastName: 'Foreign', orgId: OTHER_ORG },
}

describe('GET .../mentionables: people (org directory search)', () => {
  let f: Fixture
  afterEach(async () => {
    await f?.close()
    sinon.restore()
  })
  const open = async () => {
    f = await startFixture({ collaboration: { mentions: true, users } })
  }
  const people = async (who: 'A' | 'B', q: string, kind: 'chat' | 'agent' = 'chat') =>
    (await f.http.call('GET', f.path(kind, `/mentionables?q=${encodeURIComponent(q)}`), asUser(who))).body.items.filter((i: any) => i.type === 'user')
  const names = (items: any[]): string[] => items.map((i) => i.label)

  it('a solo chat offers org colleagues by first, middle and last name and by email, marked as not in the chat, with their address', async () => {
    await open()
    for (const q of ['john', 'mich', 'smi', 'john.s', 'JOHN.SMITH@']) {
      const found = await people('A', q)
      expect(found.find((i: any) => i.id === JOHN), q).to.deep.equal({ type: 'user', id: JOHN, label: 'John Smith', inChat: false, email: 'john.smith@acme.test' })
    }
  })

  it('every token must match some name part: "jo sm" finds Joanna Smythe and John Smith (alphabetical) but not John Disabled', async () => {
    await open()
    expect(names(await people('A', 'jo sm'))).to.deep.equal(['Joanna Smythe', 'John Smith'])
    expect(names(await people('A', 'mich smi'))).to.deep.equal(['John Smith'])
    expect(names(await people('A', 'smi zzz'))).to.deep.equal([])
  })

  it('never offers the caller, a disabled user, a service user or another org’s user', async () => {
    await open()
    const found = await people('A', 'john')
    expect(found.map((i: any) => i.id)).to.not.include.members([OFF, BOT, FOREIGN])
    expect(names(await people('A', 'alice'))).to.deep.equal([])
  })

  it('an empty query offers no one outside the chat', async () => {
    await open()
    expect(await people('A', '')).to.deep.equal([])
  })

  it('a user with no name is labelled by their email rather than dropped', async () => {
    await open()
    const [found] = await people('A', 'zed')
    expect(found).to.deep.equal({ type: 'user', id: NAMELESS, label: 'zed@acme.test', inChat: false, email: 'zed@acme.test' })
  })

  it('ranks people in the chat first, then first-name matches, then alphabetically', async () => {
    await open()
    await f.http.call('PUT', f.path('chat', '/collaborators'), asUser('A'), { collaborators: [{ ...user('B', 'write') }, { principalType: 'user', principalId: JOANNA, accessLevel: 'read' }] })
    const found = await people('A', 's')
    // Joanna Smythe is in the chat; John Smith is not (last-name match), Bob Brown is in the chat but does not match.
    expect(found.map((i: any) => [i.label, i.inChat])).to.deep.equal([
      ['Joanna Smythe', true],
      ['John Smith', false],
    ])
    const all = await people('A', 'jo')
    expect(all.map((i: any) => [i.label, i.inChat])).to.deep.equal([
      ['Joanna Smythe', true],
      ['John Smith', false],
    ])
  })

  it('participants match with the same rule: by a middle name or an email too', async () => {
    await open()
    await f.http.call('PUT', f.path('chat', '/collaborators'), asUser('A'), { collaborators: [{ principalType: 'user', principalId: JOHN, accessLevel: 'write' }] })
    const found = await people('A', 'michael')
    expect(found).to.have.length(1)
    expect(found[0]).to.include({ id: JOHN, inChat: true })
  })

  it('I-9: a caller who may not invite sees the owner only and no directory results', async () => {
    await open()
    await f.http.call('PUT', f.path('chat', '/collaborators'), asUser('A'), { collaborators: [user('B', 'read'), { principalType: 'user', principalId: JOHN, accessLevel: 'read' }] })
    expect(names(await people('B', ''))).to.deep.equal(['Alice Anderson'])
    expect(names(await people('B', 'jo'))).to.deep.equal([])
    expect(names(await people('B', 'ali'))).to.deep.equal(['Alice Anderson'])
  })

  it('the same search works in an agent chat', async () => {
    await open()
    expect(names(await people('A', 'smy', 'agent'))).to.deep.equal(['Joanna Smythe'])
  })

  it('PEOPLE keeps the fixture honest', () => {
    expect(PEOPLE.A).to.not.equal(undefined)
  })
})
