import 'reflect-metadata'
import { expect } from 'chai'
import { COUNTER_VIOLATIONS_SQL, counterViolations, expectedCredentials, verifyCredentialRows } from '../../../src/scripts/docstore-migrate/verify-identity'
import { MAPPINGS } from '../../../src/scripts/docstore-migrate/mapping'
import { ADMIN_A, MEMBER_A, SERVICE_A, USER_B, identityDocs } from './fixtures/identity'

describe('verifier parts for credentials and counters', () => {
  const docs = identityDocs()

  it('expects one credential per live account, the newest, and none for a deleted credential or a gone user', () => {
    const expected = expectedCredentials(docs.userCredentials, docs.users)
    const ids = expected.map((d) => String(d._id)).sort()
    expect(ids).to.deep.equal([String(ADMIN_A), String(MEMBER_A), String(USER_B)].sort())
    expect(ids).to.not.include(String(SERVICE_A))
    expect(expected.find((d) => d._id === String(MEMBER_A))!.hashedPassword).to.equal('$2b$10$newerhash')
  })

  it('every expected credential is reported missing when the table is empty', () => {
    const missing = verifyCredentialRows(docs.userCredentials, docs.users, [])
    expect(missing.map((d) => d.kind)).to.deep.equal(['missing_in_pg', 'missing_in_pg', 'missing_in_pg'])
    expect(missing.map((d) => d.id).sort()).to.deep.equal([String(ADMIN_A), String(MEMBER_A), String(USER_B)].sort())
  })

  it('collects the problems each counter check returns', async () => {
    const seen: string[] = []
    const problems = await counterViolations(async (sql) => (seen.push(sql), seen.length === 2 ? [{ problem: 'user counter of o is not above its slug user-9' }] : []))
    expect(seen).to.deep.equal(COUNTER_VIOLATIONS_SQL)
    expect(problems).to.deep.equal(['user counter of o is not above its slug user-9'])
  })

  it('mappings that key a row by something else name the source key; counters are checked elsewhere', () => {
    const by = (c: string) => MAPPINGS.find((m) => m.collection === c)!
    expect(by('usedPasswordResetLinks').sourceKey!({ _id: 'abc' })).to.equal('abc')
    expect(by('slack_conversations').sourceKey!({ _id: 'def' })).to.equal('def')
    expect(by('counters').skip!({})).to.contain('counterViolations')
  })
})
