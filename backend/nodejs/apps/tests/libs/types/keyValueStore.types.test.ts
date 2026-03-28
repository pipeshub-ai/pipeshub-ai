import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'

describe('libs/types/keyValueStore.types', () => {
  afterEach(() => {
    sinon.restore()
  })

  let mod: typeof import('../../../src/libs/types/keyValueStore.types')

  before(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    mod = require('../../../src/libs/types/keyValueStore.types')
  })

  it('should be importable without errors', () => {
    expect(mod).to.exist
  })

  describe('IKVStoreConnection interface', () => {
    it('should allow implementing the interface', () => {
      const mockConnection: import('../../../src/libs/types/keyValueStore.types').IKVStoreConnection = {
        connect: async () => {},
        disconnect: async () => {},
        isConnected: () => true,
      }
      expect(mockConnection.connect).to.be.a('function')
      expect(mockConnection.disconnect).to.be.a('function')
      expect(mockConnection.isConnected).to.be.a('function')
      expect(mockConnection.isConnected()).to.be.true
    })
  })
})
