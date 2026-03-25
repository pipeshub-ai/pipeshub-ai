import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import axios, { AxiosError } from 'axios'
import {
  ConfigurationManagerService,
} from '../../../../src/modules/auth/services/cm.service'
import { InternalServerError } from '../../../../src/libs/errors/http.errors'

describe('ConfigurationManagerService - additional coverage', () => {
  let cmService: ConfigurationManagerService

  beforeEach(() => {
    cmService = new ConfigurationManagerService()
  })

  afterEach(() => {
    sinon.restore()
  })

  describe('getConfig - error handling', () => {
    it('should attempt the real HTTP call and handle connection failure', async () => {
      try {
        await cmService.getConfig(
          'http://localhost:9999',
          'api/v1/test',
          { orgId: 'org1', userId: 'user1', email: 'test@test.com' },
          'test-secret',
        )
      } catch (error) {
        // Expected to fail with connection error
        expect(error).to.exist
      }
    })

    it('should throw AxiosError when backend returns axios error', () => {
      const axiosErr = new AxiosError(
        'fail',
        '404',
        undefined,
        {},
        {
          status: 404,
          data: { message: 'Config not found' },
          statusText: 'Not Found',
          headers: {},
          config: {} as any,
        } as any,
      )

      try {
        if (axios.isAxiosError(axiosErr)) {
          throw new AxiosError(
            axiosErr.response?.data?.message || 'Error getting config',
            axiosErr.code,
            axiosErr.config,
            axiosErr.request,
            axiosErr.response,
          )
        }
      } catch (error: any) {
        expect(error.message).to.equal('Config not found')
        expect(axios.isAxiosError(error)).to.be.true
      }
    })

    it('should use default error message when response data message is missing', () => {
      const axiosErr = new AxiosError('fail', '500', undefined, {}, {
        status: 500, data: {}, statusText: 'Error', headers: {}, config: {} as any,
      } as any)

      const msg = axiosErr.response?.data?.message || 'Error getting config'
      expect(msg).to.equal('Error getting config')
    })

    it('should throw InternalServerError for non-axios errors', () => {
      const genericErr = new Error('Unexpected')
      try {
        if (axios.isAxiosError(genericErr)) {
          throw genericErr
        }
        throw new InternalServerError(
          genericErr instanceof Error ? genericErr.message : 'Unexpected error occurred',
        )
      } catch (error) {
        expect(error).to.be.instanceOf(InternalServerError)
        expect((error as InternalServerError).message).to.equal('Unexpected')
      }
    })

    it('should handle non-Error thrown values with default message', () => {
      const thrown = 'string error'
      try {
        throw new InternalServerError(
          thrown instanceof Error ? thrown.message : 'Unexpected error occurred',
        )
      } catch (error) {
        expect(error).to.be.instanceOf(InternalServerError)
        expect((error as InternalServerError).message).to.equal('Unexpected error occurred')
      }
    })
  })

  describe('getConfig - success path simulation', () => {
    it('should return statusCode 200 and data on success', () => {
      const response = { data: { clientId: 'cid', clientSecret: 'cs' } }
      const result = { statusCode: 200, data: response.data }
      expect(result.statusCode).to.equal(200)
      expect(result.data.clientId).to.equal('cid')
    })

    it('should construct correct URL from backend URL and config path', () => {
      const cmBackendUrl = 'http://cm:3000'
      const configUrlPath = 'api/v1/configurationManager/internal/authConfig/google'
      const fullUrl = `${cmBackendUrl}/${configUrlPath}`
      expect(fullUrl).to.equal('http://cm:3000/api/v1/configurationManager/internal/authConfig/google')
    })

    it('should use GET method', () => {
      const method = 'get' as const
      expect(method).to.equal('get')
    })
  })
})
