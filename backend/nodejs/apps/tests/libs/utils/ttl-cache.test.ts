import { expect } from 'chai'
import { FixedClock } from '../../../src/libs/types/clock'
import { TtlCache } from '../../../src/libs/utils/ttl-cache'

describe('TtlCache', () => {
  it('returns a value until its TTL has passed', () => {
    const clock = new FixedClock(1_000)
    const cache = new TtlCache<string>(100, 10, clock)
    cache.set('k', 'v')
    clock.advance(99)
    expect(cache.get('k')).to.equal('v')
    clock.advance(1)
    expect(cache.get('k')).to.equal(undefined)
  })

  it('keeps falsy values distinct from a miss', () => {
    const cache = new TtlCache<boolean>(100, 10, new FixedClock(0))
    cache.set('k', false)
    expect(cache.get('k')).to.equal(false)
    expect(cache.get('other')).to.equal(undefined)
  })

  it('delete removes one entry', () => {
    const cache = new TtlCache<number>(100, 10, new FixedClock(0))
    cache.set('a', 1)
    cache.set('b', 2)
    cache.delete('a')
    expect(cache.get('a')).to.equal(undefined)
    expect(cache.get('b')).to.equal(2)
  })

  it('at capacity, drops expired entries and keeps live ones', () => {
    const clock = new FixedClock(0)
    const cache = new TtlCache<number>(100, 2, clock)
    cache.set('old', 1)
    clock.advance(60)
    cache.set('live', 2)
    clock.advance(50)
    cache.set('new', 3)
    expect(cache.get('old')).to.equal(undefined)
    expect(cache.get('live')).to.equal(2)
    expect(cache.get('new')).to.equal(3)
  })

  it('at capacity with nothing expired, clears everything before storing', () => {
    const cache = new TtlCache<number>(100, 2, new FixedClock(0))
    cache.set('a', 1)
    cache.set('b', 2)
    cache.set('c', 3)
    expect(cache.get('a')).to.equal(undefined)
    expect(cache.get('b')).to.equal(undefined)
    expect(cache.get('c')).to.equal(3)
  })

  it('overwriting an existing key at capacity evicts nothing', () => {
    const cache = new TtlCache<number>(100, 2, new FixedClock(0))
    cache.set('a', 1)
    cache.set('b', 2)
    cache.set('a', 9)
    expect(cache.get('a')).to.equal(9)
    expect(cache.get('b')).to.equal(2)
  })
})
