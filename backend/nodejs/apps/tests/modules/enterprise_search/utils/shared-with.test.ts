import 'reflect-metadata'
import { expect } from 'chai'
import {
  sharedWithUserId,
  sharedWithUserIdEquals,
  persistableSharedWith,
  userSharedWithRows,
} from '../../../../src/modules/enterprise_search/utils/utils'

describe('sharedWith row helpers', () => {
  const bad = [null, undefined, {}, { userId: null }, { accessLevel: 'read' }] as any[]

  it('sharedWithUserId returns the id string, undefined for malformed rows', () => {
    expect(sharedWithUserId({ userId: 'u1' })).to.equal('u1')
    bad.forEach((row) => expect(sharedWithUserId(row)).to.be.undefined)
  })

  it('sharedWithUserIdEquals never throws on malformed rows', () => {
    expect(sharedWithUserIdEquals({ userId: 'u1' }, 'u1')).to.be.true
    expect(sharedWithUserIdEquals({ userId: 'u1' }, 'u2')).to.be.false
    bad.forEach((row) => expect(sharedWithUserIdEquals(row, 'u1')).to.be.false)
  })

  it('userSharedWithRows keeps only rows with a userId and tolerates a missing list', () => {
    const good = { userId: 'u1', accessLevel: 'write' }
    expect(userSharedWithRows([...bad, good])).to.deep.equal([good])
    expect(userSharedWithRows(undefined)).to.deep.equal([])
    expect(userSharedWithRows(null)).to.deep.equal([])
  })

  it('persistableSharedWith drops only null entries and keeps rows without a userId', () => {
    const team = { principalType: 'team', teamId: 't1', accessLevel: 'read' } as any
    const noUser = { accessLevel: 'read' }
    const good = { userId: 'u1', accessLevel: 'write' }
    expect(persistableSharedWith([null, team, undefined, noUser, good] as any[])).to.deep.equal([team, noUser, good])
    expect(persistableSharedWith(undefined)).to.deep.equal([])
    expect(persistableSharedWith(null)).to.deep.equal([])
  })
})
