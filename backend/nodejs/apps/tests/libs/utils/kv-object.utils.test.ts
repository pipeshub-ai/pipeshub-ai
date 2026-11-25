import 'reflect-metadata'
import { expect } from 'chai'
import { parseKvObject } from '../../../src/libs/utils/kv-object.utils'

describe('kv-object.utils parseKvObject', () => {
  const value = { frontend: { publicEndpoint: 'http://a' }, n: 1, list: [1, 2] }

  it('parses one JSON layer', () => {
    expect(parseKvObject(JSON.stringify(value))).to.deep.equal(value)
  })

  it('parses a JSON string of JSON (two layers) to the same object', () => {
    expect(parseKvObject(JSON.stringify(JSON.stringify(value)))).to.deep.equal(value)
  })

  it('parses an empty object in both forms', () => {
    expect(parseKvObject('{}')).to.deep.equal({})
    expect(parseKvObject('"{}"')).to.deep.equal({})
  })

  it('still throws on text that is not JSON', () => {
    expect(() => parseKvObject('not json')).to.throw(SyntaxError)
    expect(() => parseKvObject('"not json"')).to.throw(SyntaxError)
  })
})
