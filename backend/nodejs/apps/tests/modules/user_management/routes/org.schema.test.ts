import 'reflect-metadata'
import { expect } from 'chai'
import mongoose from 'mongoose'
import {
  CreationBody,
  DocumentResponseSchema,
  UpdateBody,
  UpdateValidationSchema,
  DeleteResponseSchema,
  UpdateDetailsResponseSchema,
  CheckExistenceResponseSchema,
  GetOnboardingStatusResponseSchema,
  UpdateOnboardingStatusResponseSchema,
  HealthResponseSchema,
  UpdateLogoResponseSchema,
  RemoveLogoResponseSchema,
  LogoPutValidationSchema,
  LogoReadDeleteValidationSchema,
} from '../../../../src/modules/user_management/validation/org.schemas'
import { createMockOrgUpdateJson } from '../../../helpers/mock-org-updated-document'

describe('CreationBody Zod Schema', () => {
  const validCreationBody = {
    accountType: 'business' as const,
    contactEmail: 'admin@example.com',
    adminFullName: 'Admin User',
    password: 'ValidPass1!',
    registeredName: 'Acme Corp',
  }

  describe('valid inputs', () => {
    it('should accept valid business account body', () => {
      const result = CreationBody.safeParse(validCreationBody)
      expect(result.success).to.be.true
    })

    it('should accept optional shortName', () => {
      const result = CreationBody.safeParse({
        ...validCreationBody,
        shortName: 'acme',
      })
      expect(result.success).to.be.true
    })

    it('should accept optional sendEmail', () => {
      const result = CreationBody.safeParse({
        ...validCreationBody,
        sendEmail: true,
      })
      expect(result.success).to.be.true
    })

    it('should accept optional permanentAddress', () => {
      const result = CreationBody.safeParse({
        ...validCreationBody,
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
      const result = CreationBody.safeParse({
        ...validCreationBody,
        permanentAddress: { city: 'Springfield' },
      })
      expect(result.success).to.be.true
    })

    it('should reject individual accountType', () => {
      const result = CreationBody.safeParse({
        accountType: 'individual',
        contactEmail: 'test@example.com',
        adminFullName: 'Test User',
        password: 'ValidPass1!',
        registeredName: 'Test Org',
      })
      expect(result.success).to.be.false
    })
  })

  describe('accountType validation', () => {
    it('should reject missing accountType', () => {
      const { accountType, ...body } = validCreationBody
      const result = CreationBody.safeParse(body)
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('accountType'),
        )
        expect(fieldError).to.exist
      }
    })

    it('should reject invalid accountType value', () => {
      const result = CreationBody.safeParse({
        ...validCreationBody,
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
      const result = CreationBody.safeParse({
        ...validCreationBody,
        accountType: 123,
      })
      expect(result.success).to.be.false
    })
  })

  describe('contactEmail validation', () => {
    it('should reject missing contactEmail', () => {
      const { contactEmail, ...body } = validCreationBody
      const result = CreationBody.safeParse(body)
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('contactEmail'),
        )
        expect(fieldError).to.exist
      }
    })

    it('should reject invalid email format without @', () => {
      const result = CreationBody.safeParse({
        ...validCreationBody,
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
      const result = CreationBody.safeParse({
        ...validCreationBody,
        contactEmail: 'user@',
      })
      expect(result.success).to.be.false
    })

    it('should reject empty string email', () => {
      const result = CreationBody.safeParse({
        ...validCreationBody,
        contactEmail: '',
      })
      expect(result.success).to.be.false
    })

    it('should reject numeric contactEmail', () => {
      const result = CreationBody.safeParse({
        ...validCreationBody,
        contactEmail: 12345,
      })
      expect(result.success).to.be.false
    })
  })

  describe('password validation', () => {
    it('should reject missing password', () => {
      const { password, ...body } = validCreationBody
      const result = CreationBody.safeParse(body)
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('password'),
        )
        expect(fieldError).to.exist
      }
    })

    it('should reject password shorter than 8 characters', () => {
      const result = CreationBody.safeParse({
        ...validCreationBody,
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
      const result = CreationBody.safeParse({
        ...validCreationBody,
        password: '',
      })
      expect(result.success).to.be.false
    })

    it('should accept password with exactly 8 characters', () => {
      const result = CreationBody.safeParse({
        ...validCreationBody,
        password: '12345678',
      })
      expect(result.success).to.be.true
    })

    it('should reject numeric password', () => {
      const result = CreationBody.safeParse({
        ...validCreationBody,
        password: 12345678,
      })
      expect(result.success).to.be.false
    })
  })

  describe('adminFullName validation', () => {
    it('should reject missing adminFullName', () => {
      const { adminFullName, ...body } = validCreationBody
      const result = CreationBody.safeParse(body)
      expect(result.success).to.be.false
      if (!result.success) {
        const fieldError = result.error.issues.find(
          (i) => i.path.includes('adminFullName'),
        )
        expect(fieldError).to.exist
      }
    })

    it('should reject empty string adminFullName', () => {
      const result = CreationBody.safeParse({
        ...validCreationBody,
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
      const result = CreationBody.safeParse({
        ...validCreationBody,
        adminFullName: 'A',
      })
      expect(result.success).to.be.true
    })
  })

  describe('registeredName conditional validation', () => {
    it('should require registeredName when accountType is business', () => {
      const result = CreationBody.safeParse({
        accountType: 'business',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
        registeredName: '',
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

    it('should accept registeredName for business accounts', () => {
      const result = CreationBody.safeParse({
        accountType: 'business',
        contactEmail: 'admin@example.com',
        adminFullName: 'Admin User',
        password: 'ValidPass1!',
        registeredName: 'Acme Corp',
      })
      expect(result.success).to.be.true
    })

    it('should reject empty string registeredName for business accounts', () => {
      const result = CreationBody.safeParse({
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
      const result = CreationBody.safeParse({})
      expect(result.success).to.be.false
      if (!result.success) {
        expect(result.error.issues.length).to.be.greaterThan(1)
      }
    })

    it('should reject completely empty body', () => {
      const result = CreationBody.safeParse({})
      expect(result.success).to.be.false
    })
  })
})

describe('UpdateBody Zod Schema', () => {
  const validUpdateBody = {
    registeredName: 'Example Company Ltd',
    shortName: 'EX',
    contactEmail: 'contact@example.com',
    permanentAddress: {
      addressLine1: '',
      city: '',
      state: '',
      postCode: '',
      country: '',
    },
    dataCollectionConsent: true,
  }

  describe('valid inputs', () => {
    it('should accept a full valid org update body', () => {
      const result = UpdateBody.safeParse(validUpdateBody)
      expect(result.success).to.be.true
    })

    it('should accept an empty body (partial update)', () => {
      const result = UpdateBody.safeParse({})
      expect(result.success).to.be.true
    })

    it('should accept only contactEmail', () => {
      const result = UpdateBody.safeParse({
        contactEmail: 'admin@example.com',
      })
      expect(result.success).to.be.true
    })

    it('should accept only registeredName and shortName', () => {
      const result = UpdateBody.safeParse({
        registeredName: 'Acme',
        shortName: 'AC',
      })
      expect(result.success).to.be.true
    })

    it('should accept empty strings for registeredName and shortName', () => {
      const result = UpdateBody.safeParse({
        registeredName: '',
        shortName: '',
      })
      expect(result.success).to.be.true
    })

    it('should accept dataCollectionConsent false', () => {
      const result = UpdateBody.safeParse({
        ...validUpdateBody,
        dataCollectionConsent: false,
      })
      expect(result.success).to.be.true
    })

    it('should accept permanentAddress with only some fields set', () => {
      const result = UpdateBody.safeParse({
        permanentAddress: { city: 'Berlin', country: 'DE' },
      })
      expect(result.success).to.be.true
    })

    it('should accept permanentAddress with all string fields empty', () => {
      const result = UpdateBody.safeParse({
        permanentAddress: {
          addressLine1: '',
          city: '',
          state: '',
          postCode: '',
          country: '',
        },
      })
      expect(result.success).to.be.true
    })
  })

  describe('contactEmail validation', () => {
    it('should reject invalid contactEmail when provided', () => {
      const result = UpdateBody.safeParse({
        ...validUpdateBody,
        contactEmail: 'not-an-email',
      })
      expect(result.success).to.be.false
      if (!result.success) {
        const issue = result.error.issues.find((i) =>
          i.path.includes('contactEmail'),
        )
        expect(issue).to.exist
      }
    })

    it('should accept a valid plus-address email', () => {
      const result = UpdateBody.safeParse({
        contactEmail: 'user+tag@sub.example.co.uk',
      })
      expect(result.success).to.be.true
    })

    it('should reject numeric contactEmail', () => {
      const result = UpdateBody.safeParse({
        contactEmail: 123 as unknown as string,
      })
      expect(result.success).to.be.false
    })
  })

  describe('dataCollectionConsent validation', () => {
    it('should reject non-boolean dataCollectionConsent', () => {
      const result = UpdateBody.safeParse({
        ...validUpdateBody,
        dataCollectionConsent: 'yes',
      })
      expect(result.success).to.be.false
    })

    it('should reject numeric dataCollectionConsent', () => {
      const result = UpdateBody.safeParse({
        dataCollectionConsent: 1,
      })
      expect(result.success).to.be.false
    })
  })

  describe('permanentAddress validation', () => {
    it('should reject permanentAddress when it is not an object', () => {
      const result = UpdateBody.safeParse({
        ...validUpdateBody,
        permanentAddress: '123 Main St',
      })
      expect(result.success).to.be.false
    })

    it('should reject when a permanentAddress field is not a string', () => {
      const result = UpdateBody.safeParse({
        permanentAddress: { city: 12345 },
      })
      expect(result.success).to.be.false
    })

    it('should reject null for permanentAddress', () => {
      const result = UpdateBody.safeParse({
        permanentAddress: null,
      })
      expect(result.success).to.be.false
    })
  })

  describe('string field types', () => {
    it('should reject registeredName when not a string', () => {
      const result = UpdateBody.safeParse({ registeredName: 123 })
      expect(result.success).to.be.false
    })

    it('should reject shortName when not a string', () => {
      const result = UpdateBody.safeParse({ shortName: ['XX'] })
      expect(result.success).to.be.false
    })
  })

  describe('unknown keys', () => {
    it('should strip unknown keys from the body (strict known fields only)', () => {
      const result = UpdateBody.safeParse({
        ...validUpdateBody,
        futureField: 'stripped',
      })
      expect(result.success).to.be.true
      if (result.success) {
        expect(
          (result.data as { futureField?: string }).futureField,
        ).to.be.undefined
      }
    })
  })

  describe('UpdateValidationSchema (middleware shape)', () => {
    it('should parse a request-shaped object with body, query, params, headers', async () => {
      const result = await UpdateValidationSchema.parseAsync({
        body: validUpdateBody,
        query: {},
        params: {},
        headers: {},
      })
      expect(result.body.contactEmail).to.equal(validUpdateBody.contactEmail)
      expect(result.body.registeredName).to.equal(
        validUpdateBody.registeredName,
      )
    })

    it('should accept empty body in validation schema', async () => {
      const result = await UpdateValidationSchema.parseAsync({
        body: {},
        query: {},
        params: {},
        headers: {},
      })
      expect(result.body).to.deep.equal({})
    })

    it('should reject invalid body email in validation schema', async () => {
      try {
        await UpdateValidationSchema.parseAsync({
          body: { contactEmail: 'bad' },
          query: {},
          params: {},
          headers: {},
        })
        expect.fail('expected validation to throw')
      } catch (e: unknown) {
        expect(e).to.exist
      }
    })

    it('should reject extra keys on query', async () => {
      try {
        await UpdateValidationSchema.parseAsync({
          body: {},
          query: { unexpected: 'x' },
          params: {},
          headers: {},
        })
        expect.fail('expected validation to throw')
      } catch (e: unknown) {
        expect(e).to.exist
      }
    })

    it('should reject extra keys on params', async () => {
      try {
        await UpdateValidationSchema.parseAsync({
          body: {},
          query: {},
          params: { id: 'x' },
          headers: {},
        })
        expect.fail('expected validation to throw')
      } catch (e: unknown) {
        expect(e).to.exist
      }
    })
  })
})

describe('UpdateDetailsResponseSchema', () => {
  it('should accept a typical success payload with nested permanentAddress', () => {
    const result = UpdateDetailsResponseSchema.safeParse({
      message: 'Organization updated successfully',
      data: createMockOrgUpdateJson({
        permanentAddress: {
          addressLine1: '',
          city: '',
          state: '',
          postCode: '',
          country: '',
          _id: '507f191e810c19729de860ea',
        },
      }),
    })
    expect(result.success).to.be.true
  })

  it('should accept payload with extra data fields (passthrough)', () => {
    const result = UpdateDetailsResponseSchema.safeParse({
      message: 'Organization updated successfully',
      data: createMockOrgUpdateJson({ phoneNumber: '+15551234567' }),
    })
    expect(result.success).to.be.true
  })

  it('should reject empty message', () => {
    const result = UpdateDetailsResponseSchema.safeParse({
      message: '',
      data: createMockOrgUpdateJson(),
    })
    expect(result.success).to.be.false
  })

  it('should reject data missing required org fields', () => {
    const result = UpdateDetailsResponseSchema.safeParse({
      message: 'Organization updated successfully',
      data: { _id: '507f1f77bcf86cd799439012' },
    })
    expect(result.success).to.be.false
  })
})

describe('DeleteResponseSchema', () => {
  it('should accept soft-delete success payload with isDeleted true', () => {
    const result = DeleteResponseSchema.safeParse({
      message: 'Organization marked as deleted successfully',
      data: createMockOrgUpdateJson({
        isDeleted: true,
        permanentAddress: {
          addressLine1: '',
          city: '',
          state: '',
          postCode: '',
          country: '',
          _id: '507f191e810c19729de860ea',
        },
      }),
    })
    expect(result.success).to.be.true
  })

  it('should reject empty message', () => {
    const result = DeleteResponseSchema.safeParse({
      message: '',
      data: createMockOrgUpdateJson({ isDeleted: true }),
    })
    expect(result.success).to.be.false
  })
})

describe('DocumentResponseSchema', () => {
  it('should coerce Mongoose ObjectId on _id', () => {
    const result = DocumentResponseSchema.safeParse({
      _id: new mongoose.Types.ObjectId(),
      registeredName: '',
      domain: 'example.com',
      contactEmail: 'contact@example.com',
      accountType: 'business',
      onBoardingStatus: 'notConfigured',
      isDeleted: false,
      createdAt: '2026-01-01T00:00:00.000Z',
      updatedAt: '2026-01-01T00:00:00.000Z',
      slug: 'org-1',
      __v: 0,
    })
    expect(result.success).to.be.true
    if (result.success) {
      expect(typeof result.data._id).to.equal('string')
    }
  })

  it('should accept GET/POST org body with empty shortName and nested address _id', () => {
    const result = DocumentResponseSchema.safeParse(
      createMockOrgUpdateJson({
        shortName: '',
        onBoardingStatus: 'notConfigured',
        permanentAddress: {
          addressLine1: '',
          city: '',
          state: '',
          postCode: '',
          country: '',
          _id: '507f191e810c19729de860ea',
        },
      }),
    )
    expect(result.success).to.be.true
  })
})

describe('CheckExistenceResponseSchema', () => {
  it('should accept exists true or false', () => {
    expect(
      CheckExistenceResponseSchema.safeParse({ exists: true }).success,
    ).to.be.true
    expect(
      CheckExistenceResponseSchema.safeParse({ exists: false }).success,
    ).to.be.true
  })

  it('should reject non-boolean exists', () => {
    const result = CheckExistenceResponseSchema.safeParse({
      exists: 'yes',
    })
    expect(result.success).to.be.false
  })
})

describe('GetOnboardingStatusResponseSchema', () => {
  it('should accept configured status', () => {
    const result = GetOnboardingStatusResponseSchema.safeParse({
      status: 'configured',
    })
    expect(result.success).to.be.true
  })

  it('should reject invalid status string', () => {
    const result = GetOnboardingStatusResponseSchema.safeParse({
      status: 'pending',
    })
    expect(result.success).to.be.false
  })
})

describe('UpdateOnboardingStatusResponseSchema', () => {
  it('should accept success payload', () => {
    const result = UpdateOnboardingStatusResponseSchema.safeParse({
      message: 'Onboarding status updated successfully',
      status: 'configured',
    })
    expect(result.success).to.be.true
  })

  it('should reject empty message', () => {
    const result = UpdateOnboardingStatusResponseSchema.safeParse({
      message: '',
      status: 'configured',
    })
    expect(result.success).to.be.false
  })
})

describe('HealthResponseSchema', () => {
  it('should accept health payload', () => {
    const result = HealthResponseSchema.safeParse({
      status: 'healthy',
      timestamp: '2026-04-01T12:00:00.000Z',
    })
    expect(result.success).to.be.true
  })

  it('should reject non-healthy status', () => {
    const result = HealthResponseSchema.safeParse({
      status: 'degraded',
      timestamp: '2026-04-01T12:00:00.000Z',
    })
    expect(result.success).to.be.false
  })
})

describe('UpdateLogoResponseSchema', () => {
  it('should accept jpeg and svg mime responses', () => {
    expect(
      UpdateLogoResponseSchema.safeParse({
        message: 'Logo updated successfully',
        mimeType: 'image/jpeg',
      }).success,
    ).to.be.true
    expect(
      UpdateLogoResponseSchema.safeParse({
        message: 'Logo updated successfully',
        mimeType: 'image/svg+xml',
      }).success,
    ).to.be.true
  })

  it('should reject png in response (controller normalizes to jpeg/svg)', () => {
    const result = UpdateLogoResponseSchema.safeParse({
      message: 'Logo updated successfully',
      mimeType: 'image/png',
    })
    expect(result.success).to.be.false
  })

  it('should reject empty message', () => {
    const result = UpdateLogoResponseSchema.safeParse({
      message: '',
      mimeType: 'image/jpeg',
    })
    expect(result.success).to.be.false
  })
})

describe('RemoveLogoResponseSchema', () => {
  it('should accept cleared logo document (string ids)', () => {
    const result = RemoveLogoResponseSchema.safeParse({
      _id: '507f1f77bcf86cd799439011',
      orgId: '507f1f77bcf86cd799439012',
      logo: null,
      mimeType: null,
      __v: 0,
    })
    expect(result.success).to.be.true
  })

  it('should accept Mongoose ObjectId instances from toJSON()', () => {
    const oid = new mongoose.Types.ObjectId()
    const result = RemoveLogoResponseSchema.safeParse({
      _id: oid,
      orgId: oid,
      logo: null,
      mimeType: null,
      __v: 0,
    })
    expect(result.success).to.be.true
    if (result.success) {
      expect(result.data._id).to.be.a('string')
      expect(result.data.orgId).to.be.a('string')
    }
  })

  it('should reject when logo not null', () => {
    const result = RemoveLogoResponseSchema.safeParse({
      logo: 'data',
      mimeType: null,
    })
    expect(result.success).to.be.false
  })
})

describe('LogoPutValidationSchema', () => {
  it('should accept processor-shaped body', async () => {
    const data = {
      body: {
        fileBuffer: {
          buffer: Buffer.from('x'),
          mimetype: 'image/png' as const,
          originalname: 'a.png',
          size: 1,
          lastModified: 1,
          filePath: 'a.png',
        },
      },
      query: {},
      params: {},
      headers: {},
    }
    const result = await LogoPutValidationSchema.parseAsync(data)
    expect(result.body.fileBuffer.mimetype).to.equal('image/png')
  })

  it('should reject invalid mimetype', () => {
    const data = {
      body: {
        fileBuffer: {
          buffer: Buffer.from('x'),
          mimetype: 'application/pdf',
        },
      },
      query: {},
      params: {},
      headers: {},
    }
    const result = LogoPutValidationSchema.safeParse(data)
    expect(result.success).to.be.false
  })
})

describe('LogoReadDeleteValidationSchema', () => {
  it('should accept empty request parts', async () => {
    const result = await LogoReadDeleteValidationSchema.parseAsync({
      body: {},
      query: {},
      params: {},
      headers: {},
    })
    expect(result.body).to.deep.equal({})
  })
})
