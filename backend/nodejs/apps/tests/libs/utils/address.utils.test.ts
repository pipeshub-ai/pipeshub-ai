import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { Address } from '../../../src/libs/utils/address.utils'
import { jurisdictions } from '../../../src/libs/utils/juridiction.utils'

describe('address.utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('Address interface', () => {
    it('should allow creating an Address with all fields', () => {
      const address: Address = {
        addressLine1: '123 Main St',
        city: 'San Francisco',
        state: 'CA',
        postCode: '94102',
        country: jurisdictions['United States'],
      }
      expect(address.addressLine1).to.equal('123 Main St')
      expect(address.city).to.equal('San Francisco')
      expect(address.state).to.equal('CA')
      expect(address.postCode).to.equal('94102')
      expect(address.country).to.equal(jurisdictions['United States'])
    })

    it('should allow creating an Address with no fields (all optional)', () => {
      const address: Address = {}
      expect(address.addressLine1).to.be.undefined
      expect(address.city).to.be.undefined
      expect(address.state).to.be.undefined
      expect(address.postCode).to.be.undefined
      expect(address.country).to.be.undefined
    })

    it('should allow creating an Address with partial fields', () => {
      const address: Address = {
        city: 'London',
        country: jurisdictions['United Kingdom'],
      }
      expect(address.city).to.equal('London')
      expect(address.country).to.equal(jurisdictions['United Kingdom'])
      expect(address.addressLine1).to.be.undefined
    })
  })
})
