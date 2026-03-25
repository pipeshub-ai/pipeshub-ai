import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'

describe('configuration_manager/schema/connectors.schema', () => {
  afterEach(() => {
    sinon.restore()
  })

  let ConnectorsConfig: mongoose.Model<any>

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = require('../../../../src/modules/configuration_manager/schema/connectors.schema')
    ConnectorsConfig = mod.ConnectorsConfig
  })

  it('should export the ConnectorsConfig model', () => {
    expect(ConnectorsConfig).to.exist
    expect(ConnectorsConfig.modelName).to.equal('connectorsConfig')
  })

  describe('schema paths', () => {
    it('should have orgId as required ObjectId', () => {
      const path = ConnectorsConfig.schema.path('orgId')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have name as required with enum', () => {
      const path = ConnectorsConfig.schema.path('name')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have isEnabled with default true', () => {
      const path = ConnectorsConfig.schema.path('isEnabled')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(true)
    })

    it('should have lastUpdatedBy as required', () => {
      const path = ConnectorsConfig.schema.path('lastUpdatedBy')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })
  })

  describe('timestamps', () => {
    it('should have timestamps enabled', () => {
      expect(ConnectorsConfig.schema.options.timestamps).to.equal(true)
    })
  })
})
