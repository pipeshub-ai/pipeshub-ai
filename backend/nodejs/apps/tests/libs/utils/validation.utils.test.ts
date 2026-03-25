import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { z, ZodError } from 'zod'
import { ValidationUtils } from '../../../src/libs/utils/validation.utils'

describe('validation.utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('ValidationUtils.formatZodError', () => {
    it('should format a single invalid_type error', () => {
      const schema = z.object({ name: z.string() })
      try {
        schema.parse({ name: 123 })
        expect.fail('Should have thrown')
      } catch (err) {
        const result = ValidationUtils.formatZodError(err as ZodError)
        expect(result).to.have.length(1)
        expect(result[0]!.field).to.equal('name')
        expect(result[0]!.code).to.equal('INVALID_TYPE')
        expect(result[0]!.message).to.be.a('string')
        expect(result[0]!.value).to.equal('')
      }
    })

    it('should format multiple errors', () => {
      const schema = z.object({
        name: z.string(),
        age: z.number(),
      })
      try {
        schema.parse({ name: 123, age: 'not-a-number' })
        expect.fail('Should have thrown')
      } catch (err) {
        const result = ValidationUtils.formatZodError(err as ZodError)
        expect(result).to.have.length(2)
        expect(result.map((r) => r.field)).to.include.members(['name', 'age'])
      }
    })

    it('should format nested path errors with dot notation', () => {
      const schema = z.object({
        user: z.object({
          profile: z.object({
            email: z.string().email(),
          }),
        }),
      })
      try {
        schema.parse({ user: { profile: { email: 'not-an-email' } } })
        expect.fail('Should have thrown')
      } catch (err) {
        const result = ValidationUtils.formatZodError(err as ZodError)
        expect(result).to.have.length(1)
        expect(result[0]!.field).to.equal('user.profile.email')
        expect(result[0]!.code).to.equal('INVALID_STRING')
      }
    })

    it('should map too_small code', () => {
      const schema = z.object({ name: z.string().min(3) })
      try {
        schema.parse({ name: 'ab' })
        expect.fail('Should have thrown')
      } catch (err) {
        const result = ValidationUtils.formatZodError(err as ZodError)
        expect(result[0]!.code).to.equal('TOO_SMALL')
      }
    })

    it('should map too_big code', () => {
      const schema = z.object({ name: z.string().max(3) })
      try {
        schema.parse({ name: 'abcd' })
        expect.fail('Should have thrown')
      } catch (err) {
        const result = ValidationUtils.formatZodError(err as ZodError)
        expect(result[0]!.code).to.equal('TOO_BIG')
      }
    })

    it('should map invalid_enum_value code', () => {
      const schema = z.object({ color: z.enum(['red', 'blue', 'green']) })
      try {
        schema.parse({ color: 'yellow' })
        expect.fail('Should have thrown')
      } catch (err) {
        const result = ValidationUtils.formatZodError(err as ZodError)
        expect(result[0]!.code).to.equal('INVALID_ENUM')
      }
    })

    it('should map invalid_date code', () => {
      const schema = z.object({ date: z.date() })
      try {
        schema.parse({ date: new Date('invalid') })
        expect.fail('Should have thrown')
      } catch (err) {
        const result = ValidationUtils.formatZodError(err as ZodError)
        expect(result[0]!.code).to.equal('INVALID_DATE')
      }
    })

    it('should default to VALIDATION_ERROR for unknown codes', () => {
      const zodError = new ZodError([
        {
          code: 'unrecognized_keys' as any,
          keys: ['extra'],
          path: ['data'],
          message: 'Unrecognized key',
        } as any,
      ])
      const result = ValidationUtils.formatZodError(zodError)
      expect(result[0]!.code).to.equal('VALIDATION_ERROR')
    })

    it('should always set value to empty string', () => {
      const schema = z.object({ x: z.number() })
      try {
        schema.parse({ x: 'string' })
        expect.fail('Should have thrown')
      } catch (err) {
        const result = ValidationUtils.formatZodError(err as ZodError)
        expect(result[0]!.value).to.equal('')
      }
    })

    it('should handle empty path (root-level validation)', () => {
      const schema = z.string()
      try {
        schema.parse(123)
        expect.fail('Should have thrown')
      } catch (err) {
        const result = ValidationUtils.formatZodError(err as ZodError)
        expect(result[0]!.field).to.equal('')
      }
    })

    it('should map invalid_literal code', () => {
      const schema = z.literal('hello')
      try {
        schema.parse('world')
        expect.fail('Should have thrown')
      } catch (err) {
        const result = ValidationUtils.formatZodError(err as ZodError)
        expect(result[0]!.code).to.equal('INVALID_LITERAL')
      }
    })

    it('should map custom code', () => {
      const schema = z.string().refine((val) => val === 'exact', {
        message: 'Must be exact',
      })
      try {
        schema.parse('nope')
        expect.fail('Should have thrown')
      } catch (err) {
        const result = ValidationUtils.formatZodError(err as ZodError)
        expect(result[0]!.code).to.equal('CUSTOM')
        expect(result[0]!.message).to.equal('Must be exact')
      }
    })
  })
})
