import { expect } from 'chai'
import {
  EPOCH_KEY_PREFIX,
  EPOCH_READ_RETRY_MS,
  EPOCH_READ_TIMEOUT_MS,
  connectorEpochKey,
  failFastEpochSource,
  kbEpochKey,
  principalEpochKey,
  readEpochs,
  redisEpochSource,
  unavailableEpochSource,
} from '../../../../../../src/modules/enterprise_search/services/conversation/privacy/epochs'
import { settlesAt } from '../../../../../helpers/settles-at'

const ORG = '6566c3a6f0a1b2c3d4e5f601'
const DANA = '6566c3a6f0a1b2c3d4e5f6aa'

describe('permission epochs: the keys', () => {
  // The AI backend builds the same strings (app/services/permissions/epochs.py); both sides are pinned to them.
  it('names one counter per connector, per person and for the collections of an organisation', () => {
    expect(connectorEpochKey(ORG, 'conn-1')).to.equal(`state:epoch:ge:${ORG}:conn-1`)
    expect(principalEpochKey(ORG, DANA)).to.equal(`state:epoch:pe:${ORG}:${DANA}`)
    expect(kbEpochKey(ORG)).to.equal(`state:epoch:ge:${ORG}:kb`)
    expect(EPOCH_KEY_PREFIX).to.equal('state:epoch:')
  })

  it('refuses an id that is empty or would run into the next part of the key', () => {
    expect(() => connectorEpochKey('', 'conn-1')).to.throw()
    expect(() => connectorEpochKey(ORG, '')).to.throw()
    expect(() => principalEpochKey(ORG, 'a:b')).to.throw()
    expect(() => kbEpochKey('o:rg')).to.throw()
  })
})

describe('permission epochs: reading them', () => {
  const fakeRedis = (values: Record<string, string>, fail = false) => {
    const asked: string[][] = []
    const client = {
      mget: async (...keys: string[]) => {
        asked.push(keys)
        if (fail) throw new Error('connection reset')
        return keys.map((k) => values[k] ?? null)
      },
    }
    return { asked, provider: (namespace = '') => ({ keyNamespace: namespace, getClient: () => client as never }) }
  }

  it('reads every counter in one request, under the namespace both sides share', async () => {
    const keys = [connectorEpochKey(ORG, 'conn-1'), principalEpochKey(ORG, DANA)]
    const redis = fakeRedis({ [`ns-a:${keys[0]}`]: '1731000000000001', [`ns-a:${keys[1]}`]: '1731000000000007' })
    const epochs = await readEpochs(redisEpochSource(redis.provider('ns-a')), keys)
    expect(redis.asked).to.deep.equal([[`ns-a:${keys[0]}`, `ns-a:${keys[1]}`]])
    expect(epochs?.get(keys[0] as string)).to.equal(1731000000000001)
    expect(epochs?.get(keys[1] as string)).to.equal(1731000000000007)
  })

  it('a counter nobody moved yet reads as zero', async () => {
    const key = kbEpochKey(ORG)
    const epochs = await readEpochs(redisEpochSource(fakeRedis({}).provider()), [key])
    expect(epochs?.get(key)).to.equal(0)
  })

  it('when the counters cannot be read the answer is "unknown", never a guess', async () => {
    const key = kbEpochKey(ORG)
    expect(await readEpochs(redisEpochSource(fakeRedis({}, true).provider()), [key])).to.equal(null)
  })

  it('a value that is not a counter makes the read unknown', async () => {
    const key = kbEpochKey(ORG)
    expect(await readEpochs(redisEpochSource(fakeRedis({ [key]: 'not-a-number' }).provider()), [key])).to.equal(null)
  })

  it('without a store both sides share, nothing can be read', async () => {
    expect(await readEpochs(unavailableEpochSource, [kbEpochKey(ORG)])).to.equal(null)
  })

  it('no keys read nothing', async () => {
    const redis = fakeRedis({})
    expect((await readEpochs(redisEpochSource(redis.provider()), []))?.size).to.equal(0)
    expect(redis.asked).to.deep.equal([])
  })
})

describe('permission epochs: a store that hangs', () => {
  const KEYS = [principalEpochKey(ORG, DANA)]
  const hanging = () => {
    let calls = 0
    return {
      source: { values: () => { calls += 1; return new Promise<never>(() => undefined) } },
      calls: () => calls,
    }
  }

  it('holds a read no longer than its deadline, and the read is "unknown"', async () => {
    const store = hanging()
    expect(await settlesAt(EPOCH_READ_TIMEOUT_MS, () => readEpochs(failFastEpochSource(store.source), KEYS))).to.equal(null)
    expect(EPOCH_READ_TIMEOUT_MS).to.be.at.most(250)
  })

  it('after a failure skips the store for a while, then lets one read try it again', async () => {
    const store = hanging()
    let now = 1_000
    const source = failFastEpochSource(store.source, () => now)
    expect(await readEpochs(source, KEYS)).to.equal(null)
    expect(store.calls()).to.equal(1)
    for (let i = 0; i < 5; i += 1) expect(await readEpochs(source, KEYS)).to.equal(null)
    expect(store.calls(), 'skipped while the window lasts').to.equal(1)
    now += EPOCH_READ_RETRY_MS
    const values = { [KEYS[0] as string]: '7' }
    const answering = failFastEpochSource({ values: async (keys) => keys.map((k) => values[k] ?? null) }, () => now)
    expect(await readEpochs(answering, KEYS)).to.deep.equal(new Map([[KEYS[0], 7]]))
    expect(await readEpochs(source, KEYS)).to.equal(null)
    expect(store.calls(), 'one read tries again once the window ends').to.equal(2)
  })

  it('passes a store that answers straight through', async () => {
    const source = failFastEpochSource({ values: async (keys) => keys.map(() => '3') })
    expect(await readEpochs(source, KEYS)).to.deep.equal(new Map([[KEYS[0], 3]]))
  })
})
