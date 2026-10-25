import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { z, ZodError } from 'zod'
import { emailCheck, guidCheck, integerCheck, urlCheck } from '../../../src/libs/utils/zod-formats'
import { ValidationUtils, messageWhenMissing, messageWhenPresent } from '../../../src/libs/utils/validation.utils'

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

  describe('ValidationUtils.formatValidationErrorMessage', () => {
    it('should join multiple issue messages with newlines', () => {
      const message = ValidationUtils.formatValidationErrorMessage([
        { field: 'body.name', message: 'Name is required', code: 'TOO_SMALL', value: '' },
        { field: 'body.models', message: 'At least one reasoning model is required', code: 'CUSTOM', value: '' },
      ])

      expect(message).to.equal('Name is required\nAt least one reasoning model is required')
    })

    it('should fall back when no issue messages are present', () => {
      const message = ValidationUtils.formatValidationErrorMessage([
        { field: 'body.name', message: '', code: 'TOO_SMALL', value: '' },
      ])

      expect(message).to.equal("Some of the information sent isn't valid. Check the form and try again.")
    })
  })

  describe('friendly wording for stock Zod messages', () => {
    const messagesFor = (schema: z.ZodTypeAny, value: unknown): string[] => {
      const result = schema.safeParse(value)
      if (result.success) throw new Error('expected validation to fail')
      return ValidationUtils.formatZodError(result.error).map((e) => e.message)
    }

    it('names the field and says what is wrong, without the request part', () => {
      const schema = z.object({
        body: z.object({
          name: z.string(),
          pageSize: z.number(),
          email: z.string().email(),
          title: z.string().min(1),
          role: z.enum(['admin', 'member']),
          tags: z.array(z.string()).min(2),
        }),
      })
      expect(
        messagesFor(schema, { body: { pageSize: 'x', email: 'no', title: '', role: 'boss', tags: ['a'] } }),
      ).to.have.members([
        'Name is required.',
        'Page size must be a number.',
        'Email must be a valid email address.',
        "Title can't be empty.",
        'Role must be one of: admin, member.',
        'Add at least 2 items to tags.',
      ])
    })

    it('treats only the first path entry as the request part', () => {
      const schema = z.object({ query: z.object({ query: z.string() }) })
      expect(messagesFor(schema, { query: {} })).to.deep.equal(['Query is required.'])
    })

    it('keeps a message the schema wrote itself', () => {
      const schema = z.object({ colour: z.string(messageWhenMissing('Pick a colour first')) })
      expect(messagesFor(schema, {})).to.deep.equal(['Pick a colour first'])
    })

    it('keeps the schema message for a missing value only (messageWhenMissing)', () => {
      const schema = z.object({ colour: z.string(messageWhenMissing('Pick a colour first')) })
      expect(messagesFor(schema, {})).to.deep.equal(['Pick a colour first'])
      expect(messagesFor(schema, { colour: 5 })).to.deep.equal(['Colour must be text.'])
    })

    it('keeps the schema message for a supplied value only (messageWhenPresent)', () => {
      const schema = z.object({ version: z.number(messageWhenPresent('version must be an integer')) })
      expect(messagesFor(schema, { version: 'x' })).to.deep.equal(['version must be an integer'])
      expect(messagesFor(schema, {})).to.deep.equal(['Version is required.'])
    })

    it('reads a single-option enum and a literal as "isn\'t valid" without a schema message', () => {
      const schema = z.object({ kind: z.literal('a') })
      expect(messagesFor(schema, { kind: 'b' })).to.deep.equal(["Kind isn't valid."])
    })

    it('reports an invalid Date as INVALID_DATE', () => {
      const schema = z.object({ when: z.date() })
      const result = schema.safeParse({ when: new Date('nope') })
      const details = ValidationUtils.formatZodError(result.error as ZodError)
      expect(details[0]!.code).to.equal('INVALID_DATE')
      expect(details[0]!.message).to.equal("When isn't valid.")
    })

    it('reads a missing or wrong-typed enum as a type error, as zod 3 did', () => {
      const schema = z.object({ role: z.enum(['admin', 'member']) })
      const detail = (value: unknown) => {
        const parsed = schema.safeParse(value, { reportInput: true })
        return ValidationUtils.formatZodError(parsed.error as ZodError).map((d) => [d.code, d.message])
      }
      expect(detail({})).to.deep.equal([['INVALID_TYPE', 'Role is required.']])
      expect(detail({ role: null })).to.deep.equal([['INVALID_TYPE', 'Role is required.']])
      expect(detail({ role: 5 })).to.deep.equal([['INVALID_TYPE', "Role must be a 'admin' | 'member'."]])
      expect(detail({ role: 'boss' })).to.deep.equal([['INVALID_ENUM', 'Role must be one of: admin, member.']])
    })

    it('keeps a schema message on a missing enum but reports the type code', () => {
      const schema = z.object({ mode: z.enum(['a', 'b'], { error: 'Invalid mode' }) })
      const parsed = schema.safeParse({}, { reportInput: true })
      const [d] = ValidationUtils.formatZodError(parsed.error as ZodError)
      expect([d!.code, d!.message]).to.deep.equal(['INVALID_TYPE', 'Invalid mode'])
    })

    it('lets a missing or wrong-type value win over a format message', () => {
      const schema = z.object({
        email: z.string().check(emailCheck('Invalid email format')),
        url: z.string().check(urlCheck('Bad url')),
        id: z.string().check(guidCheck('Bad id')),
      })
      expect(messagesFor(schema, {})).to.have.members(['Email is required.', 'Url is required.', 'Id is required.'])
      expect(messagesFor(schema, { email: 5, url: null, id: [] })).to.have.members(['Email must be text.', 'Url is required.', 'Id must be text.'])
      expect(messagesFor(schema, { email: 'x', url: 'y', id: 'z' })).to.have.members(['Invalid email format', 'Bad url', 'Bad id'])
    })

    it('treats the old zod wording used as a schema message as stock', () => {
      const schema = z.object({ email: z.string().check(emailCheck('Invalid email')) })
      expect(messagesFor(schema, { email: 'x' })).to.deep.equal(['Email must be a valid email address.'])
    })

    it('does not add check lines after a type error and does not repeat a line', () => {
      const schema = z.object({ title: z.string().min(1), tags: z.array(z.string()).max(2) })
      expect(messagesFor(schema, { title: [], tags: 'abc' })).to.deep.equal(['Title must be text.', 'Tags must be a list.'])
    })

    it('keeps the minimum line next to a fraction on a whole-number field', () => {
      const schema = z.object({ n: z.number().check(integerCheck()).min(1) })
      expect(messagesFor(schema, { n: 0.5 })).to.have.members(['N must be a whole number.', 'N must be at least 1.'])
      expect(schema.safeParse({ n: 1e21 }).success).to.equal(true)
    })

    it('names records and whole numbers in plain words', () => {
      const schema = z.object({ tools: z.record(z.string(), z.string()), count: z.number().int() })
      expect(messagesFor(schema, { tools: 'x', count: 'y' })).to.have.members(['Tools must be a group of fields.', 'Count must be a number.'])
    })

    it('falls back to "The request" when the whole body is wrong', () => {
      const schema = z.object({ body: z.object({ name: z.string() }) })
      expect(messagesFor(schema, { body: 'nope' })).to.deep.equal(['The request must be a group of fields.'])
    })
  })
})
