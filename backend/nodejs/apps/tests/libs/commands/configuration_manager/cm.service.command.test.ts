import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  ConfigurationManagerServiceCommand,
  ConfigurationManagerCommandOptions,
} from '../../../../src/libs/commands/configuration_manager/cm.service.command'
import { HttpMethod } from '../../../../src/libs/enums/http-methods.enum'

describe('ConfigurationManagerServiceCommand', () => {
  let fetchStub: sinon.SinonStub

  beforeEach(() => {
    fetchStub = sinon.stub(global, 'fetch')
  })

  afterEach(() => {
    sinon.restore()
  })

  function makeFetchResponse(
    status: number,
    body: any,
    statusText = 'OK',
  ): Response {
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText,
      headers: new Headers(),
      json: sinon.stub().resolves(body),
      text: sinon.stub().resolves(JSON.stringify(body)),
    } as unknown as Response
  }

  describe('constructor', () => {
    it('should create an instance with all options', () => {
      const cmd = new ConfigurationManagerServiceCommand({
        uri: 'http://cm.local/config',
        method: HttpMethod.GET,
        headers: { authorization: 'Bearer tok' },
        queryParams: { key: 'val' },
        body: { setting: 'value' },
      })
      expect(cmd).to.be.instanceOf(ConfigurationManagerServiceCommand)
    })

    it('should create an instance with minimal options', () => {
      const cmd = new ConfigurationManagerServiceCommand({
        uri: 'http://cm.local/config',
        method: HttpMethod.GET,
        headers: {},
      })
      expect(cmd).to.be.instanceOf(ConfigurationManagerServiceCommand)
    })
  })

  describe('execute', () => {
    it('should return structured response with statusCode, data, and msg on success', async () => {
      const responseData = { config: { key: 'value' } }
      fetchStub.resolves(makeFetchResponse(200, responseData, 'OK'))

      const cmd = new ConfigurationManagerServiceCommand({
        uri: 'http://cm.local/config',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(200)
      expect(result.data).to.deep.equal(responseData)
      expect(result.msg).to.equal('OK')
    })

    it('should build URL with query params', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new ConfigurationManagerServiceCommand({
        uri: 'http://cm.local/config',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
        queryParams: { env: 'production', version: 2 },
      })

      await cmd.execute()
      const url = fetchStub.firstCall.args[0]
      expect(url).to.include('env=production')
      expect(url).to.include('version=2')
    })

    it('should include body in request options when body is provided', async () => {
      fetchStub.resolves(makeFetchResponse(200, { updated: true }))

      const cmd = new ConfigurationManagerServiceCommand({
        uri: 'http://cm.local/config',
        method: HttpMethod.PUT,
        headers: { 'content-type': 'application/json' },
        body: { setting: 'new-value' },
      })

      await cmd.execute()
      const reqOptions = fetchStub.firstCall.args[1]
      expect(reqOptions.body).to.equal(
        JSON.stringify({ setting: 'new-value' }),
      )
    })

    it('should not include body in request options when body is undefined', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new ConfigurationManagerServiceCommand({
        uri: 'http://cm.local/config',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
      })

      await cmd.execute()
      const reqOptions = fetchStub.firstCall.args[1]
      expect(reqOptions.body).to.be.undefined
    })

    it('should use the correct HTTP method', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const methods = [
        HttpMethod.GET,
        HttpMethod.POST,
        HttpMethod.PUT,
        HttpMethod.PATCH,
        HttpMethod.DELETE,
      ]

      for (const method of methods) {
        fetchStub.resetHistory()
        const cmd = new ConfigurationManagerServiceCommand({
          uri: 'http://cm.local/config',
          method,
          headers: { 'content-type': 'application/json' },
        })

        await cmd.execute()
        expect(fetchStub.firstCall.args[1].method).to.equal(method)
      }
    })

    it('should retry on failure and succeed on retry', async () => {
      fetchStub.onFirstCall().rejects(new Error('timeout'))
      fetchStub.onSecondCall().resolves(makeFetchResponse(200, { ok: true }))

      const cmd = new ConfigurationManagerServiceCommand({
        uri: 'http://cm.local/config',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(200)
      expect(fetchStub.calledTwice).to.be.true
    })

    it('should throw after exhausting retries', async () => {
      fetchStub.rejects(new Error('unavailable'))

      const cmd = new ConfigurationManagerServiceCommand({
        uri: 'http://cm.local/config',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
      })

      try {
        await cmd.execute()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('unavailable')
      }
    })

    it('should handle non-200 status codes without throwing', async () => {
      fetchStub.resolves(
        makeFetchResponse(
          500,
          { error: 'internal server error' },
          'Internal Server Error',
        ),
      )

      const cmd = new ConfigurationManagerServiceCommand({
        uri: 'http://cm.local/config',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(500)
      expect(result.msg).to.equal('Internal Server Error')
    })

    it('should sanitize headers and strip disallowed ones', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new ConfigurationManagerServiceCommand({
        uri: 'http://cm.local/config',
        method: HttpMethod.GET,
        headers: {
          authorization: 'Bearer test',
          'x-forwarded-for': '1.2.3.4',
          'content-type': 'application/json',
        },
      })

      await cmd.execute()
      const reqHeaders = fetchStub.firstCall.args[1].headers
      expect(reqHeaders).to.have.property('authorization')
      expect(reqHeaders).to.have.property('content-type')
      expect(reqHeaders).not.to.have.property('x-forwarded-for')
    })
  })
})
