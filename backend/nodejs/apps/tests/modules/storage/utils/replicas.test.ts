import { expect } from 'chai'
import { appReplicaCeiling, configuredLocalStorageReplicaProblem, localStorageReplicaProblem } from '../../../../src/modules/storage/utils/replicas'

describe('storage/utils/replicas', () => {
  it('reads the replica ceiling the Helm chart sets', () => {
    expect(appReplicaCeiling({ PIPESHUB_APP_MAX_REPLICAS: '6' })).to.equal(6)
  })

  for (const value of [undefined, '', '1', '0', '-3', 'abc']) {
    it(`treats ${JSON.stringify(value)} as a single replica (Compose, dev)`, () => {
      const env = value === undefined ? {} : { PIPESHUB_APP_MAX_REPLICAS: value }
      expect(appReplicaCeiling(env)).to.equal(1)
      expect(localStorageReplicaProblem(env)).to.equal(null)
    })
  }

  it('explains the problem with more than one replica', () => {
    const problem = localStorageReplicaProblem({ PIPESHUB_APP_MAX_REPLICAS: '2' })
    expect(problem).to.contain('up to 2 app replicas')
    expect(problem).to.contain('S3 or Azure Blob')
  })

  for (const [what, stored] of [
    ['one layer', JSON.stringify({ storageType: 's3' })],
    ['a JSON string of JSON (older Python writer)', JSON.stringify(JSON.stringify({ storageType: 's3' }))],
  ] as const) {
    it(`reads the saved storage type stored as ${what}`, async () => {
      const kv = { get: async () => stored } as never
      expect(await configuredLocalStorageReplicaProblem(kv, { PIPESHUB_APP_MAX_REPLICAS: '2' })).to.equal(null)
    })
  }
})
