import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { addStyling } from '../../../../src/modules/mail/utils/styling'

describe('mail/utils/styling', () => {
  afterEach(() => {
    sinon.restore()
  })

  describe('addStyling', () => {
    it('should merge styling properties with template data', () => {
      const templateData = { name: 'John', otp: '1234' }
      const result = addStyling(templateData)

      expect(result).to.have.property('name', 'John')
      expect(result).to.have.property('otp', '1234')
      expect(result).to.have.property('containerstyle')
      expect(result).to.have.property('bodytablestyle')
      expect(result).to.have.property('divstyle')
      expect(result).to.have.property('pstyle')
      expect(result).to.have.property('buttonstyle')
      expect(result).to.have.property('tabledata')
      expect(result).to.have.property('tablestyle')
    })

    it('should include style attributes in containerstyle', () => {
      const result = addStyling({})
      expect(result.containerstyle).to.include('style=')
      expect(result.containerstyle).to.include('background-color')
    })

    it('should include style attributes in buttonstyle', () => {
      const result = addStyling({})
      expect(result.buttonstyle).to.include('style=')
      expect(result.buttonstyle).to.include('cursor: pointer')
    })

    it('should not overwrite template data', () => {
      const templateData = { custom: 'value' }
      const result = addStyling(templateData)
      expect(result.custom).to.equal('value')
    })

    it('should handle empty template data', () => {
      const result = addStyling({})
      expect(result).to.have.property('containerstyle')
    })
  })
})
