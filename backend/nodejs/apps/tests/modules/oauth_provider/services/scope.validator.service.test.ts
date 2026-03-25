import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { ScopeValidatorService } from '../../../../src/modules/oauth_provider/services/scope.validator.service'
import { InvalidScopeError } from '../../../../src/libs/errors/oauth.errors'

describe('ScopeValidatorService', () => {
  let service: ScopeValidatorService

  beforeEach(() => {
    service = new ScopeValidatorService()
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('validateRequestedScopes', () => {
    it('should not throw for valid scopes', () => {
      expect(() => service.validateRequestedScopes(['org:read'])).to.not.throw()
    })

    it('should throw InvalidScopeError for invalid scopes', () => {
      try {
        service.validateRequestedScopes(['completely:invalid:scope:xyz'])
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidScopeError)
      }
    })
  })

  describe('validateScopesForApp', () => {
    it('should not throw when all requested scopes are allowed', () => {
      expect(() => service.validateScopesForApp(['org:read'], ['org:read', 'org:write'])).to.not.throw()
    })

    it('should throw InvalidScopeError when scopes not allowed for app', () => {
      try {
        service.validateScopesForApp(['org:read', 'org:admin'], ['org:read'])
        expect.fail('Should have thrown')
      } catch (error) {
        expect(error).to.be.instanceOf(InvalidScopeError)
        expect((error as InvalidScopeError).message).to.include('not allowed')
      }
    })
  })

  describe('parseScopes', () => {
    it('should parse space-separated scope string', () => {
      const result = service.parseScopes('org:read org:write')
      expect(result).to.deep.equal(['org:read', 'org:write'])
    })

    it('should return empty array for empty string', () => {
      expect(service.parseScopes('')).to.deep.equal([])
    })

    it('should return empty array for whitespace-only string', () => {
      expect(service.parseScopes('   ')).to.deep.equal([])
    })

    it('should handle multiple spaces between scopes', () => {
      const result = service.parseScopes('org:read    org:write')
      expect(result).to.deep.equal(['org:read', 'org:write'])
    })
  })

  describe('scopesToString', () => {
    it('should join scopes with space', () => {
      expect(service.scopesToString(['org:read', 'org:write'])).to.equal('org:read org:write')
    })

    it('should return empty string for empty array', () => {
      expect(service.scopesToString([])).to.equal('')
    })
  })

  describe('getAllScopes', () => {
    it('should return an array of scope definitions', () => {
      const scopes = service.getAllScopes()
      expect(scopes).to.be.an('array')
      expect(scopes.length).to.be.greaterThan(0)
      expect(scopes[0]).to.have.property('name')
      expect(scopes[0]).to.have.property('description')
      expect(scopes[0]).to.have.property('category')
    })
  })

  describe('getScopesGroupedByCategory', () => {
    it('should return scopes grouped by category', () => {
      const grouped = service.getScopesGroupedByCategory()
      expect(grouped).to.be.an('object')
      const keys = Object.keys(grouped)
      expect(keys.length).to.be.greaterThan(0)
    })
  })

  describe('getScopeDefinitions', () => {
    it('should return definitions for valid scopes', () => {
      const defs = service.getScopeDefinitions(['org:read'])
      expect(defs).to.have.lengthOf(1)
      expect(defs[0].name).to.equal('org:read')
    })

    it('should filter out undefined definitions for unknown scopes', () => {
      const defs = service.getScopeDefinitions(['org:read', 'unknown:scope'])
      expect(defs).to.have.lengthOf(1)
    })
  })

  describe('requiresConsent', () => {
    it('should return true when any scope requires consent', () => {
      const result = service.requiresConsent(['org:read'])
      expect(result).to.be.true
    })
  })

  describe('getConsentRequiredScopes', () => {
    it('should return scopes that require consent', () => {
      const result = service.getConsentRequiredScopes(['org:read'])
      expect(result).to.include('org:read')
    })
  })

  describe('hasScope', () => {
    it('should return true when scope is present', () => {
      expect(service.hasScope(['org:read', 'org:write'], 'org:read')).to.be.true
    })

    it('should return false when scope is not present', () => {
      expect(service.hasScope(['org:read'], 'org:write')).to.be.false
    })
  })

  describe('hasAllScopes', () => {
    it('should return true when all scopes are present', () => {
      expect(service.hasAllScopes(['org:read', 'org:write'], ['org:read', 'org:write'])).to.be.true
    })

    it('should return false when not all scopes are present', () => {
      expect(service.hasAllScopes(['org:read'], ['org:read', 'org:write'])).to.be.false
    })
  })

  describe('hasAnyScope', () => {
    it('should return true when at least one scope is present', () => {
      expect(service.hasAnyScope(['org:read'], ['org:read', 'org:write'])).to.be.true
    })

    it('should return false when no scopes match', () => {
      expect(service.hasAnyScope(['org:read'], ['org:admin'])).to.be.false
    })
  })

  describe('getGrantedScopes', () => {
    it('should return intersection of requested and allowed scopes', () => {
      const result = service.getGrantedScopes(
        ['org:read', 'org:write', 'org:admin'],
        ['org:read', 'org:admin'],
      )
      expect(result).to.deep.equal(['org:read', 'org:admin'])
    })

    it('should return empty array when no overlap', () => {
      const result = service.getGrantedScopes(['org:read'], ['org:write'])
      expect(result).to.deep.equal([])
    })
  })
})
