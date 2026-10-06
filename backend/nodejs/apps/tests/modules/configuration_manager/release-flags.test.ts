import { expect } from 'chai'
import { FixedClock, IClock } from '../../../src/libs/types/clock'
import { PLATFORM_FEATURE_FLAGS } from '../../../src/modules/configuration_manager/constants/constants'
import {
  MAX_LIVE_RELEASE_FLAGS,
  RELEASE_FLAGS,
  ReleaseFlag,
} from '../../../src/modules/configuration_manager/constants/release-flags'

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

function expiredFlags(flags: readonly ReleaseFlag[], clock: IClock): string[] {
  return flags
    .filter((f) => clock.now() > Date.parse(`${f.removeBy}T23:59:59.999Z`))
    .map(
      (f) =>
        `Release flag ${f.key} (owner ${f.owner}) passed removeBy ${f.removeBy}: ` +
        'remove the flag and its legacy branch, or extend removeBy with a written justification.',
    )
}

describe('release flag registry (ADR-006 time bomb)', () => {
  const clock = new FixedClock(Date.now())

  it('PH02-19: no live release flag is past its removeBy date', () => {
    expect(expiredFlags(RELEASE_FLAGS, clock)).to.deep.equal([])
  })

  it('PH02-19: detects an expired entry with removal guidance', () => {
    const entry: ReleaseFlag = {
      key: 'ENABLE_X',
      owner: 'someone',
      createdAt: '2026-01-01',
      removeBy: '2026-06-30',
    }
    const errors = expiredFlags([entry], new FixedClock(Date.parse('2026-07-01T00:00:00Z')))
    expect(errors).to.have.length(1)
    expect(errors[0]).to.match(/remove the flag and its legacy branch/)
    expect(expiredFlags([entry], new FixedClock(Date.parse('2026-06-30T12:00:00Z')))).to.deep.equal([])
  })

  it('PH02-19: every entry has an owner and ISO dates in order', () => {
    for (const f of RELEASE_FLAGS) {
      expect(f.owner, f.key).to.be.a('string').and.not.equal('')
      expect(f.createdAt, f.key).to.match(ISO_DATE)
      expect(f.removeBy, f.key).to.match(ISO_DATE)
      expect(Number.isNaN(Date.parse(f.removeBy)), f.key).to.equal(false)
      expect(Date.parse(f.removeBy), f.key).to.be.greaterThan(Date.parse(f.createdAt))
    }
  })

  it('PH02-20: every release flag is a default-off platform flag shown in Labs', () => {
    for (const f of RELEASE_FLAGS) {
      const def = PLATFORM_FEATURE_FLAGS.find((d) => d.key === f.key)
      expect(def, `${f.key} missing from PLATFORM_FEATURE_FLAGS`).to.exist
      expect(def!.hidden ?? false, f.key).to.equal(false)
      expect(def!.defaultEnabled, f.key).to.equal(false)
    }
  })

  it('PH02-20: at most 3 live release flags with unique keys', () => {
    expect(RELEASE_FLAGS.length).to.be.at.most(MAX_LIVE_RELEASE_FLAGS)
    expect(new Set(RELEASE_FLAGS.map((f) => f.key)).size).to.equal(RELEASE_FLAGS.length)
  })
})
