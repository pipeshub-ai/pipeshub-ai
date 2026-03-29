import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { jurisdictions } from '../../../src/libs/utils/juridiction.utils'

describe('juridiction.utils', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('jurisdictions enum', () => {
    it('should contain United States', () => {
      expect(jurisdictions).to.have.property('United States')
    })

    it('should contain United Kingdom', () => {
      expect(jurisdictions).to.have.property('United Kingdom')
    })

    it('should contain India', () => {
      expect(jurisdictions).to.have.property('India')
    })

    it('should contain Germany', () => {
      expect(jurisdictions).to.have.property('Germany')
    })

    it('should contain Japan', () => {
      expect(jurisdictions).to.have.property('Japan')
    })

    it('should contain Australia', () => {
      expect(jurisdictions).to.have.property('Australia')
    })

    it('should contain Singapore', () => {
      expect(jurisdictions).to.have.property('Singapore')
    })

    it('should contain Grenada as last member', () => {
      expect(jurisdictions).to.have.property('Grenada')
    })

    it('should have numeric values (TypeScript numeric enum)', () => {
      // TypeScript numeric enums have both forward (name->number) and reverse (number->name) mappings
      expect(typeof jurisdictions['United States']).to.equal('number')
      expect(typeof jurisdictions[0]).to.equal('string')
    })

    it('should have bidirectional mapping for first member', () => {
      const idx = jurisdictions['United States']
      expect(jurisdictions[idx]).to.equal('United States')
    })

    it('should have correct index for first few members', () => {
      expect(jurisdictions['United States']).to.equal(0)
      expect(jurisdictions['China']).to.equal(1)
      expect(jurisdictions['Japan']).to.equal(2)
      expect(jurisdictions['Germany']).to.equal(3)
      expect(jurisdictions['India']).to.equal(4)
    })

    it('should contain countries with special characters in name', () => {
      expect(jurisdictions).to.have.property('Czech Republic (Czechia)')
      expect(jurisdictions).to.have.property('Saint Kitts & Nevis')
    })

    it('should contain many jurisdictions', () => {
      // Count string keys only (forward mappings)
      const stringKeys = Object.keys(jurisdictions).filter(
        (k) => isNaN(Number(k)),
      )
      expect(stringKeys.length).to.be.greaterThan(80)
    })
  })
})
