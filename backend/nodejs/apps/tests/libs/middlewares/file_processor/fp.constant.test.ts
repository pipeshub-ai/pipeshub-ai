import 'reflect-metadata'
import { expect } from 'chai'
import { FileProcessingType, FileProcessingMode } from '../../../../src/libs/middlewares/file_processor/fp.constant'

describe('FileProcessor Constants', () => {
  describe('FileProcessingType', () => {
    it('should have BUFFER type with value "buffer"', () => {
      expect(FileProcessingType.BUFFER).to.equal('buffer')
    })

    it('should have JSON type with value "json"', () => {
      expect(FileProcessingType.JSON).to.equal('json')
    })

    it('should only have BUFFER and JSON types', () => {
      const values = Object.values(FileProcessingType).filter(
        (v) => typeof v === 'string',
      )
      expect(values).to.have.members(['buffer', 'json'])
    })
  })

  describe('FileProcessingMode', () => {
    it('should have SINGLE mode with value "single"', () => {
      expect(FileProcessingMode.SINGLE).to.equal('single')
    })

    it('should have MULTIPLE mode with value "multiple"', () => {
      expect(FileProcessingMode.MULTIPLE).to.equal('multiple')
    })

    it('should only have SINGLE and MULTIPLE modes', () => {
      const values = Object.values(FileProcessingMode).filter(
        (v) => typeof v === 'string',
      )
      expect(values).to.have.members(['single', 'multiple'])
    })
  })
})
