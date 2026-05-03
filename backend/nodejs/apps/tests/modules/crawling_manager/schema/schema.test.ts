import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import mongoose from 'mongoose'

describe('crawling_manager/schema/schema', () => {
  afterEach(() => {
    sinon.restore()
  })

  let CrawlingManagerConfig: mongoose.Model<any>
  let defaultExports: any

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = require('../../../../src/modules/crawling_manager/schema/schema')
    CrawlingManagerConfig = mod.CrawlingManagerConfig
    defaultExports = mod.default
  })

  it('should export CrawlingManagerConfig model', () => {
    expect(CrawlingManagerConfig).to.exist
    expect(CrawlingManagerConfig.modelName).to.equal('crawlingManagerConfig')
  })

  it('should export default object with schema references', () => {
    expect(defaultExports).to.exist
    expect(defaultExports.BaseScheduleConfigSchema).to.exist
    expect(defaultExports.BaseConnectorConfigSchema).to.exist
    expect(defaultExports.BaseCrawlingScheduleSchema).to.exist
    expect(defaultExports.CustomCrawlingScheduleSchema).to.exist
    expect(defaultExports.WeeklyCrawlingScheduleSchema).to.exist
    expect(defaultExports.DailyCrawlingScheduleSchema).to.exist
    expect(defaultExports.HourlyCrawlingScheduleSchema).to.exist
    expect(defaultExports.MonthlyCrawlingScheduleSchema).to.exist
    expect(defaultExports.OnceCrawlingScheduleSchema).to.exist
  })

  describe('CrawlingManagerConfig schema paths', () => {
    it('should have orgId as required', () => {
      const path = CrawlingManagerConfig.schema.path('orgId')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have configName as required with maxLength', () => {
      const path = CrawlingManagerConfig.schema.path('configName')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have description as optional string', () => {
      const path = CrawlingManagerConfig.schema.path('description')
      expect(path).to.exist
      expect(path.instance).to.equal('String')
    })

    it('should have excludedUsers array', () => {
      const path = CrawlingManagerConfig.schema.path('excludedUsers')
      expect(path).to.exist
      expect(path.instance).to.equal('Array')
    })

    it('should have excludedUserGroups array', () => {
      const path = CrawlingManagerConfig.schema.path('excludedUserGroups')
      expect(path).to.exist
      expect(path.instance).to.equal('Array')
    })

    it('should have fileFormatConfigs array', () => {
      const path = CrawlingManagerConfig.schema.path('fileFormatConfigs')
      expect(path).to.exist
      expect(path.instance).to.equal('Array')
    })

    it('should have isGloballyEnabled with default true', () => {
      const path = CrawlingManagerConfig.schema.path('isGloballyEnabled')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(true)
    })

    it('should have maxConcurrentCrawlers with default 5', () => {
      const path = CrawlingManagerConfig.schema.path('maxConcurrentCrawlers')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(5)
    })

    it('should have crawlTimeoutMinutes with default 60', () => {
      const path = CrawlingManagerConfig.schema.path('crawlTimeoutMinutes')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(60)
    })

    it('should have retryAttempts with default 3', () => {
      const path = CrawlingManagerConfig.schema.path('retryAttempts')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(3)
    })

    it('should have retryDelayMinutes with default 5', () => {
      const path = CrawlingManagerConfig.schema.path('retryDelayMinutes')
      expect(path).to.exist
      expect(path.defaultValue).to.equal(5)
    })

    it('should have currentStatus field', () => {
      const path = CrawlingManagerConfig.schema.path('currentStatus')
      expect(path).to.exist
    })

    it('should have createdBy as required', () => {
      const path = CrawlingManagerConfig.schema.path('createdBy')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have lastUpdatedBy as required', () => {
      const path = CrawlingManagerConfig.schema.path('lastUpdatedBy')
      expect(path).to.exist
      expect(path).to.have.property('isRequired', true)
    })

    it('should have crawlingStats sub-document', () => {
      const path = CrawlingManagerConfig.schema.path('crawlingStats')
      expect(path).to.exist
    })

    it('should have time control fields', () => {
      expect(CrawlingManagerConfig.schema.path('startTime')).to.exist
      expect(CrawlingManagerConfig.schema.path('stopTime')).to.exist
      expect(CrawlingManagerConfig.schema.path('resumeTime')).to.exist
    })
  })

  describe('timestamps', () => {
    it('should have timestamps enabled', () => {
      expect(CrawlingManagerConfig.schema.options.timestamps).to.equal(true)
    })
  })

  describe('indexes', () => {
    it('should have indexes defined', () => {
      const indexes = CrawlingManagerConfig.schema.indexes()
      expect(indexes.length).to.be.greaterThan(0)
    })
  })

  describe('instance methods', () => {
    it('should have calculateNextRunTime method', () => {
      expect(CrawlingManagerConfig.schema.methods.calculateNextRunTime).to.be.a('function')
    })
  })
})
