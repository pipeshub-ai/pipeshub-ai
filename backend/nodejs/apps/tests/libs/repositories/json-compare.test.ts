import { expect } from 'chai'
import { jsonDiff, jsonEqual } from '../../../src/libs/repositories/json-compare'

describe('JSON-semantic comparator', () => {
  it('ignores key order at any depth', () => {
    expect(jsonDiff({ a: 1, b: { c: 2, d: [1, 2] } }, { b: { d: [1, 2], c: 2 }, a: 1 })).to.deep.equal([])
  })
  it('treats null and absent as different', () => {
    expect(jsonDiff({ a: 1, x: null }, { a: 1 })).to.deep.equal(['$.x: absent in right'])
    expect(jsonDiff({ a: 1 }, { a: 1, x: null })).to.deep.equal(['$.x: absent in left'])
  })
  it('treats undefined as absent, as on the wire', () => {
    expect(jsonEqual({ a: 1, x: undefined }, { a: 1 })).to.equal(true)
  })
  it('keeps JSON types strict: number is not string, epoch-ms is not ISO', () => {
    expect(jsonDiff({ __v: 0 }, { __v: '0' })).to.deep.equal(['$.__v: type number != string'])
    expect(jsonDiff({ t: 1760000000123 }, { t: '2025-10-09T08:53:20.123Z' })).to.deep.equal(['$.t: type number != string'])
  })
  it('compares dates as their ISO strings', () => {
    expect(jsonEqual({ t: new Date(1) }, { t: '1970-01-01T00:00:00.001Z' })).to.equal(true)
  })
  it('keeps array order and length', () => {
    expect(jsonDiff([1, 2], [2, 1])).to.have.length(2)
    expect(jsonDiff([1], [1, 2])).to.deep.equal(['$: length 1 != 2'])
  })
  it('reports the path of a nested difference', () => {
    expect(jsonDiff({ a: [{ b: 1 }] }, { a: [{ b: 2 }] })).to.deep.equal(['$.a[0].b: 1 != 2'])
  })
  it('compares an array with an object and null with an object as different types', () => {
    expect(jsonDiff([], {})).to.deep.equal(['$: type array != object'])
    expect(jsonDiff(null, {})).to.deep.equal(['$: type null != object'])
  })
})
