import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'

describe('enterprise_search/citations/citations.schema', () => {
  afterEach(() => {
    sinon.restore()
  })

  let Citation: mongoose.Model<any>

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = require('../../../../src/modules/enterprise_search/citations/citations.schema')
    Citation = mod.default
  })

  it('should export the Citation model as default', () => {
    expect(Citation).to.exist
    expect(Citation.modelName).to.equal('citations')
  })

  describe('schema paths', () => {
    it('should have content as required string', () => {
      const path = Citation.schema.path('content')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
      expect(path.instance).to.equal('String')
    })

    it('should have chunkIndex as required number', () => {
      const path = Citation.schema.path('chunkIndex')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have metadata as required', () => {
      const path = Citation.schema.path('metadata')
      expect(path).to.exist
    })

    it('should have citationType as required string', () => {
      const path = Citation.schema.path('citationType')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })
  })

  describe('metadata sub-schema', () => {
    it('should have orgId as required', () => {
      const metadataSchema = Citation.schema.path('metadata').schema
      expect(metadataSchema.path('orgId')).to.exist
    })

    it('should have mimeType as required', () => {
      const metadataSchema = Citation.schema.path('metadata').schema
      expect(metadataSchema.path('mimeType')).to.exist
    })

    it('should have recordId as required', () => {
      const metadataSchema = Citation.schema.path('metadata').schema
      expect(metadataSchema.path('recordId')).to.exist
    })

    it('should have recordName as required', () => {
      const metadataSchema = Citation.schema.path('metadata').schema
      expect(metadataSchema.path('recordName')).to.exist
    })

    it('should have origin as required', () => {
      const metadataSchema = Citation.schema.path('metadata').schema
      expect(metadataSchema.path('origin')).to.exist
    })

    it('should have extension as required', () => {
      const metadataSchema = Citation.schema.path('metadata').schema
      const extPath = metadataSchema.path('extension')
      expect(extPath).to.exist
      expect(extPath).to.have.property('isRequired', true)
    })

    it('should have recordVersion with default 0', () => {
      const metadataSchema = Citation.schema.path('metadata').schema
      const rvPath = metadataSchema.path('recordVersion')
      expect(rvPath).to.exist
      expect(rvPath.defaultValue).to.equal(0)
    })

    it('should have bounding_box array', () => {
      const metadataSchema = Citation.schema.path('metadata').schema
      expect(metadataSchema.path('bounding_box')).to.exist
    })
  })

  describe('static method', () => {
    it('should have createFromAIResponse static method', () => {
      expect(Citation.schema.statics.createFromAIResponse).to.be.a('function')
    })
  })

  describe('timestamps', () => {
    it('should have timestamps enabled', () => {
      expect(Citation.schema.options.timestamps).to.equal(true)
    })
  })

  describe('indexes', () => {
    it('should have indexes defined', () => {
      const indexes = Citation.schema.indexes()
      expect(indexes.length).to.be.greaterThan(0)
    })
  })
})
