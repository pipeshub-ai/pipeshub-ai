import 'reflect-metadata'
import { expect } from 'chai'
import { BadRequestError } from '../../../../src/libs/errors/http.errors'
import { requireStringParam } from '../../../../src/modules/storage/utils/paid-results'

describe('requireStringParam', () => {
  it('returns a string parameter unchanged', () => {
    expect(requireStringParam('ocr_page/1/a.json', 'path')).to.equal('ocr_page/1/a.json')
  })

  for (const [label, value] of [
    ['an array', ['a', 'b']],
    ['an object', { a: 'b' }],
    ['undefined', undefined],
    ['an empty string', ''],
    ['a number', 7],
  ] as const) {
    it(`refuses ${label} with a 400`, () => {
      expect(() => requireStringParam(value, 'path')).to.throw(BadRequestError)
    })
  }
})
