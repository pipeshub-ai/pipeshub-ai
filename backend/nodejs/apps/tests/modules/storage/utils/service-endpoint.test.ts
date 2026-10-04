import 'reflect-metadata'
import { expect } from 'chai'
import { storedServiceEndpoint } from '../../../../src/modules/storage/utils/service-endpoint'
import { endpoint } from '../../../../src/modules/storage/constants/constants'
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service'

const store = (value: string | null): KeyValueStoreService =>
  ({ get: async (key: string) => (key === endpoint ? value : null) }) as unknown as KeyValueStoreService

describe('storedServiceEndpoint', () => {
  it('returns the address stored for the service', async () => {
    const kv = store(JSON.stringify({ storage: { endpoint: 'http://storage:3000' }, cm: { endpoint: 'http://cm:3001' } }))
    expect(await storedServiceEndpoint(kv, 'storage', 'http://default')).to.equal('http://storage:3000')
    expect(await storedServiceEndpoint(kv, 'cm', 'http://default')).to.equal('http://cm:3001')
  })

  for (const [what, stored] of [
    ['nothing is stored', null],
    ['the document is empty', '{}'],
    ['the document is null', 'null'],
    ['another service has the only entry', JSON.stringify({ cm: { endpoint: 'http://cm:3001' } })],
    ['the entry is null', JSON.stringify({ storage: null })],
    ['the entry has no address', JSON.stringify({ storage: {} })],
    ['the address is empty', JSON.stringify({ storage: { endpoint: '' } })],
    ['the address is not text', JSON.stringify({ storage: { endpoint: 42 } })],
  ] as const) {
    it(`falls back when ${what}`, async () => {
      expect(await storedServiceEndpoint(store(stored), 'storage', 'http://default')).to.equal('http://default')
    })
  }
})
