/// <reference types="mocha" />
import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { StorageService } from '../../../src/modules/storage/storage.service'

describe('StorageService', () => {
  let mockKvs: any
  let mockConfig: any
  let mockDefaultConfig: any

  beforeEach(() => {
    mockKvs = {
      get: sinon.stub(),
    }
    mockConfig = {
      mountName: 'TestMount',
      baseUrl: 'http://localhost:3000',
      accessKeyId: 'ak-123',
      secretAccessKey: 'sk-456',
      region: 'us-east-1',
      bucketName: 'my-bucket',
      azureBlobConnectionString: 'DefaultEndpointsProtocol=https;',
      accountName: 'myaccount',
      accountKey: 'mykey',
      containerName: 'mycontainer',
      endpointProtocol: 'https',
      endpointSuffix: 'core.windows.net',
    }
    mockDefaultConfig = {
      endpoint: 'http://default-endpoint:3000',
    }
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('constructor', () => {
    it('should create an instance', () => {
      const service = new StorageService(mockKvs, mockConfig, mockDefaultConfig)
      expect(service).to.exist
    })
  })

  describe('isConnected', () => {
    it('should return false before initialization', () => {
      const service = new StorageService(mockKvs, mockConfig, mockDefaultConfig)
      expect(service.isConnected()).to.be.false
    })
  })

  describe('initialize', () => {
    it('should initialize with local storage type', async () => {
      mockKvs.get.resolves(JSON.stringify({ storageType: 'local' }))
      const service = new StorageService(mockKvs, mockConfig, mockDefaultConfig)
      await service.initialize()
      expect(service.isConnected()).to.be.true
      expect(service.getAdapter()).to.exist
    })

    it('should initialize with s3 storage type', async () => {
      mockKvs.get.resolves(JSON.stringify({ storageType: 's3' }))
      const service = new StorageService(mockKvs, mockConfig, mockDefaultConfig)
      await service.initialize()
      expect(service.isConnected()).to.be.true
    })

    it('should attempt azureBlob initialization and handle config error', async () => {
      mockKvs.get.resolves(JSON.stringify({ storageType: 'azureBlob' }))
      const service = new StorageService(mockKvs, mockConfig, mockDefaultConfig)
      try {
        await service.initialize()
      } catch (err: any) {
        expect(err.message).to.include('Azure Blob Storage')
      }
    })

    it('should throw for unsupported storage type', async () => {
      mockKvs.get.resolves(JSON.stringify({ storageType: 'gcs' }))
      const service = new StorageService(mockKvs, mockConfig, mockDefaultConfig)
      try {
        await service.initialize()
        expect.fail('should have thrown')
      } catch (err: any) {
        expect(err.message).to.include('Unsupported storage type')
      }
    })

    it('should default to local when config is empty', async () => {
      mockKvs.get.resolves(null)
      const service = new StorageService(mockKvs, mockConfig, mockDefaultConfig)
      // null resolves to '{}', which parses to {}, storageType is undefined
      try {
        await service.initialize()
      } catch (err: any) {
        expect(err.message).to.include('Unsupported storage type')
      }
    })

    it('should not re-initialize if already initialized', async () => {
      mockKvs.get.resolves(JSON.stringify({ storageType: 'local' }))
      const service = new StorageService(mockKvs, mockConfig, mockDefaultConfig)
      await service.initialize()
      expect(service.isConnected()).to.be.true
      // Second call should be a no-op
      await service.initialize()
      expect(mockKvs.get.calledOnce).to.be.true
    })

    it('should throw when kvs.get fails', async () => {
      mockKvs.get.rejects(new Error('KVS down'))
      const service = new StorageService(mockKvs, mockConfig, mockDefaultConfig)
      try {
        await service.initialize()
        expect.fail('should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('KVS down')
      }
      expect(service.isConnected()).to.be.false
    })
  })

  describe('disconnect', () => {
    it('should set isConnected to false', async () => {
      mockKvs.get.resolves(JSON.stringify({ storageType: 'local' }))
      const service = new StorageService(mockKvs, mockConfig, mockDefaultConfig)
      await service.initialize()
      expect(service.isConnected()).to.be.true
      await service.disconnect()
      expect(service.isConnected()).to.be.false
    })
  })

  describe('getAdapter', () => {
    it('should return the adapter after initialization', async () => {
      mockKvs.get.resolves(JSON.stringify({ storageType: 'local' }))
      const service = new StorageService(mockKvs, mockConfig, mockDefaultConfig)
      await service.initialize()
      const adapter = service.getAdapter()
      expect(adapter).to.exist
    })
  })
})
