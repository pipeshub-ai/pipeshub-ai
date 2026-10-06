import 'reflect-metadata'
import { expect } from 'chai'
import { OBJECT_ID_REGEX, TEAM_ID_REGEX, objectId, teamId } from '../../../src/libs/validators/zod-primitives'

const msg = (r: { success: boolean; error?: { issues: { message: string }[] } }): string | null =>
  r.success ? null : r.error!.issues[0]!.message

describe('zod-primitives (PH02-08)', () => {
  it('objectId defaults to "Invalid <label> format" and honours an explicit message', () => {
    expect(msg(objectId('thing ID').safeParse('xyz'))).to.equal('Invalid thing ID format')
    expect(msg(objectId('thing ID', 'custom').safeParse('xyz'))).to.equal('custom')
    expect(objectId('x').safeParse('a'.repeat(24)).success).to.equal(true)
    expect(objectId('x').safeParse('A'.repeat(24)).success).to.equal(true)
    expect(objectId('x').safeParse('a'.repeat(23)).success).to.equal(false)
    expect(objectId('x').safeParse('a'.repeat(25)).success).to.equal(false)
  })

  it('teamId accepts UUIDv4 and all_<24hex>, rejects all_x', () => {
    const t = teamId()
    expect(t.safeParse('123e4567-e89b-42d3-a456-426614174000').success).to.equal(true)
    expect(t.safeParse(`all_${'a'.repeat(24)}`).success).to.equal(true)
    expect(msg(t.safeParse('all_x'))).to.equal('Invalid team ID format')
  })

  it('exports the regexes', () => {
    expect(OBJECT_ID_REGEX.test('a'.repeat(24))).to.equal(true)
    expect(TEAM_ID_REGEX.test('all_x')).to.equal(false)
  })
})
