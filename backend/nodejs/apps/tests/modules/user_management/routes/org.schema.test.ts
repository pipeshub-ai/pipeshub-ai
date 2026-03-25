import 'reflect-metadata'
import { expect } from 'chai'
import { OrgCreationBody } from '../../../../src/modules/user_management/routes/org.routes'

describe('OrgCreationBody Zod Schema', () => {
  const validIndividualBody = {
    accountType: 'individual' as const,
    contactEmail: 'admin@example.com',
    adminFullName: 'Admin User',
    password: 'ValidPass1!',
  }

  const validBusinessBody = {
    ...validIndividualBody,
    accountType: 'business' as const,
    registeredName: 'Acme Corp',
  }

  describe('valid inputs', () => {
    it('should accept valid individual account body', () => {
      const result = OrgCreationBody.safeParse(validIndividualBody)
      expect(result.success).to.be.true
    })

    it('should accept valid business account body', () => {
      const result = OrgCreationBody.safeParse(validBusinessBody)
      expect(result.success).to.be.true
    })

    it('should accept optional shortName', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        shortName: 'acme',
      })
      expect(result.success).to.be.true
    })

    it('should accept optional sendEmail', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        sendEmail: true,
      })
      expect(result.success).to.be.true
    })

    it('should accept optional permanentAddress', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        permanentAddress: {
          addressLine1: '123 Main St',
          city: 'Springfield',
          state: 'IL',
          country: 'US',
          postCode: '62701',
        },
      })
      expect(result.success).to.be.true
    })

    it('should accept permanentAddress with partial fields', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        permanentAddress: { city: 'Springfield' },
      })
      expect(result.success).to.be.true
    })

    it('should not require registeredName for individual accounts', () => {
      const result = OrgCreationBody.safeParse({
        accountType: 'individual',
        contactEmail: 'test@example.com',
        adminFullName: 'Test User',
        password: 'ValidPass1!',
      })
      expect(result.success).to.be.true
    })
  })

  describe('accountType validation', () => {
    it('should reject missing accountType', () => {
      const { accountType, ...body } = validIndividualBody
      const result = OrgCreationBody.safeParse(body)
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('accountType'),
        )
        expect(fieldError).to.exist
      }
    })

    it('should reject invalid accountType value', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        accountType: 'enterprise',
      })
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('accountType'),
        )
        expect(fieldError).to.exist
      }
    })

    it('should reject numeric accountType', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        accountType: 123,
      })
      expect(result.success).to.be.false
    })
  })

  describe('contactEmail validation', () => {
    it('should reject missing contactEmail', () => {
      const { contactEmail, ...body } = validIndividualBody
      const result = OrgCreationBody.safeParse(body)
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('contactEmail'),
        )
        expect(fieldError).to.exist
      }
    })

    it('should reject invalid email format without @', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        contactEmail: 'not-an-email',
      })
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('contactEmail'),
        )
        expect(fieldError).to.exist
        expect(fieldError!.message).to.equal('Invalid email format')
      }
    })

    it('should reject invalid email format without domain', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        contactEmail: 'user@',
      })
      expect(result.success).to.be.false
    })

    it('should reject empty string email', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        contactEmail: '',
      })
      expect(result.success).to.be.false
    })

    it('should reject numeric contactEmail', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        contactEmail: 12345,
      })
      expect(result.success).to.be.false
    })
  })

  describe('password validation', () => {
    it('should reject missing password', () => {
      const { password, ...body } = validIndividualBody
      const result = OrgCreationBody.safeParse(body)
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('password'),
        )
        expect(fieldError).to.exist
      }
    })

    it('should reject password shorter than 8 characters', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        password: 'Ab1!xyz',
      })
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('password'),
        )
        expect(fieldError).to.exist
        expect(fieldError!.message).to.equal(
          'Minimum 8 characters password required',
        )
      }
    })

    it('should reject empty string password', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        password: '',
      })
      expect(result.success).to.be.false
    })

    it('should accept password with exactly 8 characters', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        password: '12345678',
      })
      expect(result.success).to.be.true
    })

    it('should reject numeric password', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        password: 12345678,
      })
      expect(result.success).to.be.false
    })
  })

  describe('adminFullName validation', () => {
    it('should reject missing adminFullName', () => {
      const { adminFullName, ...body } = validIndividualBody
      const result = OrgCreationBody.safeParse(body)
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('adminFullName'),
        )
        expect(fieldError).to.exist
      }
    })

    it('should reject empty string adminFullName', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        adminFullName: '',
      })
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('adminFullName'),
        )
        expect(fieldError).to.exist
        expect(fieldError!.message).to.equal('Admin full name required')
      }
    })

    it('should accept single character adminFullName', () => {
      const result = OrgCreationBody.safeParse({
        ...validIndividualBody,
        adminFullName: 'A',
      })
      expect(result.success).to.be.true
    })
  })

  describe('registeredName conditional validation', () => {
    it('should require registeredName when accountType is business', () => {
      const result = OrgCreationBody.safeParse({
        accountType: 'business',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
      })
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('registeredName'),
        )
        expect(fieldError).to.exist
        expect(fieldError!.message).to.equal(
          'Registered Name is required for business accounts',
        )
      }
    })

    it('should accept empty registeredName for individual accounts', () => {
      const result = OrgCreationBody.safeParse({
        accountType: 'individual',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
      })
      expect(result.success).to.be.true
    })

    it('should accept registeredName for business accounts', () => {
      const result = OrgCreationBody.safeParse({
        accountType: 'business',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
        registeredName: 'Acme Corp',
      })
      expect(result.success).to.be.true
    })

    it('should reject empty string registeredName for business accounts', () => {
      const result = OrgCreationBody.safeParse({
        accountType: 'business',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
        registeredName: '',
      })
      expect(result.success).to.be.false
    })
  })

  describe('multiple missing fields', () => {
    it('should report errors for all missing required fields', () => {
      const result = OrgCreationBody.safeParse({})
      expect(result.success).to.be.false
      if (!result.success) {
        expect(result.error.issues.length).to.be.greaterThan(1)
      }
    })

    it('should reject completely empty body', () => {
      const result = OrgCreationBody.safeParse({})
      expect(result.success).to.be.false
    })
  })
})
