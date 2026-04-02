import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('libs/types/validation.types', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../src/libs/types/validation.types')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../src/libs/types/validation.types')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  describe('ValidationErrorDetail interface', () => {
    it('should allow creating conforming objects', () => {
      const error: import('../../../src/libs/types/validation.types').ValidationErrorDetail = {
        field: 'email',
        message: 'Invalid email format',
        value: 'not-an-email',
        code: 'INVALID_FORMAT',
      }
      expect(error.field).to.equal('email')
      expect(error.message).to.equal('Invalid email format')
      expect(error.value).to.equal('not-an-email')
      expect(error.code).to.equal('INVALID_FORMAT')
    })

    it('should allow optional value field', () => {
      const error: import('../../../src/libs/types/validation.types').ValidationErrorDetail = {
        field: 'name',
        message: 'Name is required',
        code: 'REQUIRED',
      }
      expect(error.field).to.equal('name')
      expect(error.value).to.be.undefined
    })
  })

  describe('ValidatorOptions interface', () => {
    it('should allow creating conforming objects', () => {
      const options: import('../../../src/libs/types/validation.types').ValidatorOptions = {
        abortEarly: true,
        stripUnknown: false,
      }
      expect(options.abortEarly).to.be.true
      expect(options.stripUnknown).to.be.false
    })

    it('should allow empty options', () => {
      const options: import('../../../src/libs/types/validation.types').ValidatorOptions = {}
      expect(options.abortEarly).to.be.undefined
      expect(options.stripUnknown).to.be.undefined
    })
  })
})
