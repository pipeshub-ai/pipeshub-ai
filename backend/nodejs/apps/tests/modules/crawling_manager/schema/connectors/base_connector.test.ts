import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('crawling_manager/schema/connectors/base_connector', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../../../src/modules/crawling_manager/schema/connectors/base_connector')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../../../src/modules/crawling_manager/schema/connectors/base_connector')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  it('should allow creating objects conforming to IBaseConnectorConfig shape', () => {
    const config: import('../../../../../src/modules/crawling_manager/schema/connectors/base_connector').IBaseConnectorConfig = {
      isEnabled: true,
      lastUpdatedBy: {} as any,
    }
    expect(config.isEnabled).to.be.true
    expect(config.lastUpdatedBy).to.exist
  })

  it('should allow optional updatedAt field', () => {
    const config: import('../../../../../src/modules/crawling_manager/schema/connectors/base_connector').IBaseConnectorConfig = {
      isEnabled: false,
      lastUpdatedBy: {} as any,
      updatedAt: new Date(),
    }
    expect(config.isEnabled).to.be.false
    expect(config.updatedAt).to.be.instanceOf(Date)
  })
})
