import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'

describe('enterprise_search/schema/citation.schema', () => {
  afterEach(() => {
    sinon.restore()
  })

  let Citation: mongoose.Model<any>

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = require('../../../../src/modules/enterprise_search/schema/citation.schema')
    Citation = mod.default
  })

  it('should export the Citation model as default', () => {
    expect(Citation).to.exist
    expect(Citation.modelName).to.equal('citation')
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
      expect(path.instance).to.equal('Number')
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

    it('should have optional array fields', () => {
      const metadataSchema = Citation.schema.path('metadata').schema
      expect(metadataSchema.path('blockNum')).to.exist
      expect(metadataSchema.path('pageNum')).to.exist
      expect(metadataSchema.path('departments')).to.exist
      expect(metadataSchema.path('topics')).to.exist
      expect(metadataSchema.path('languages')).to.exist
      expect(metadataSchema.path('bounding_box')).to.exist
    })

    it('should have optional string fields', () => {
      const metadataSchema = Citation.schema.path('metadata').schema
      expect(metadataSchema.path('sheetName')).to.exist
      expect(metadataSchema.path('connector')).to.exist
      expect(metadataSchema.path('recordType')).to.exist
      expect(metadataSchema.path('blockType')).to.exist
      expect(metadataSchema.path('blockText')).to.exist
    })

    it('keeps the connector instance id, not only the kind of source', () => {
      // `connector` is e.g. SLACK, shared by every Slack instance; the UI needs
      // the instance to tell a demo connector's records from real ones.
      const metadataSchema = Citation.schema.path('metadata').schema
      expect(metadataSchema.path('connectorId')).to.exist
      expect(metadataSchema.path('connectorId').instance).to.equal('String')

      const citation = new Citation({
        content: 'c',
        chunkIndex: 1,
        citationType: 'vectordb|document',
        metadata: {
          orgId: 'o1',
          mimeType: 'text/markdown',
          recordId: 'r1',
          recordName: 'INC-2031',
          origin: 'CONNECTOR',
          connector: 'JIRA',
          connectorId: 'demo-connector-1',
        },
      })
      expect(citation.metadata.connectorId).to.equal('demo-connector-1')
    })

    it('publishes connectorId in the citation metadata the API returns', () => {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const yaml = require('js-yaml')
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { readFileSync } = require('fs')
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { join } = require('path')
      const spec = yaml.load(
        readFileSync(
          join(__dirname, '..', '..', '..', '..', 'src', 'modules', 'api-docs', 'pipeshub-openapi.yaml'),
          'utf8',
        ),
      ) as { components: { schemas: Record<string, { properties: Record<string, unknown> }> } }
      const published = spec.components.schemas.PersistedSemanticSearchCitationMetadata
      expect(published.properties).to.have.property('connectorId')
    })

    it('should have recordVersion with default 0', () => {
      const metadataSchema = Citation.schema.path('metadata').schema
      const rvPath = metadataSchema.path('recordVersion')
      expect(rvPath).to.exist
      expect(rvPath.defaultValue).to.equal(0)
    })

    it('should have previewRenderable with default true', () => {
      const metadataSchema = Citation.schema.path('metadata').schema
      const prPath = metadataSchema.path('previewRenderable')
      expect(prPath).to.exist
      expect(prPath.defaultValue).to.equal(true)
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
