import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  IAMServiceCommand,
  IAMCommandOptions,
} from '../../../../src/libs/commands/iam/iam.service.command'
import { HttpMethod } from '../../../../src/libs/enums/http-methods.enum'

describe('IAMServiceCommand', () => {
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
      const cmd = new IAMServiceCommand({
        uri: 'http://iam.local/users',
        method: HttpMethod.GET,
        headers: { authorization: 'Bearer tok' },
        queryParams: { role: 'admin' },
        body: { userId: '123' },
      })
      expect(cmd).to.be.instanceOf(IAMServiceCommand)
    })

    it('should create an instance with minimal options', () => {
      const cmd = new IAMServiceCommand({
        uri: 'http://iam.local/users',
        method: HttpMethod.GET,
        headers: {},
      })
      expect(cmd).to.be.instanceOf(IAMServiceCommand)
    })
  })

  describe('execute', () => {
    it('should return IAMResponse with statusCode, data, and msg on success', async () => {
      const responseData = { user: { id: '1', name: 'Alice' } }
      fetchStub.resolves(makeFetchResponse(200, responseData, 'OK'))

      const cmd = new IAMServiceCommand({
        uri: 'http://iam.local/users/1',
        method: HttpMethod.GET,
        headers: { authorization: 'Bearer tok' },
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(200)
      expect(result.data).to.deep.equal(responseData)
      expect(result.msg).to.equal('OK')
    })

    it('should build URL with query params', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new IAMServiceCommand({
        uri: 'http://iam.local/users',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
        queryParams: { role: 'admin', active: true },
      })

      await cmd.execute()
      const url = fetchStub.firstCall.args[0]
      expect(url).to.include('role=admin')
      expect(url).to.include('active=true')
    })

    it('should include body in request for POST method', async () => {
      fetchStub.resolves(makeFetchResponse(201, { id: 'new-user' }, 'Created'))

      const cmd = new IAMServiceCommand({
        uri: 'http://iam.local/users',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { name: 'Bob', email: 'bob@example.com' },
      })

      await cmd.execute()
      const reqOptions = fetchStub.firstCall.args[1]
      expect(reqOptions.body).to.equal(
        JSON.stringify({ name: 'Bob', email: 'bob@example.com' }),
      )
    })

    it('should not include body when body is undefined', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new IAMServiceCommand({
        uri: 'http://iam.local/users',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
      })

      await cmd.execute()
      const reqOptions = fetchStub.firstCall.args[1]
      expect(reqOptions.body).to.be.undefined
    })

    it('should use the correct HTTP method', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      for (const method of [
        HttpMethod.GET,
        HttpMethod.POST,
        HttpMethod.PUT,
        HttpMethod.PATCH,
        HttpMethod.DELETE,
      ]) {
        fetchStub.resetHistory()
        const cmd = new IAMServiceCommand({
          uri: 'http://iam.local/users',
          method,
          headers: { 'content-type': 'application/json' },
        })

        await cmd.execute()
        expect(fetchStub.firstCall.args[1].method).to.equal(method)
      }
    })

    it('should retry on failure and succeed on retry', async () => {
      fetchStub.onFirstCall().rejects(new Error('network timeout'))
      fetchStub.onSecondCall().resolves(makeFetchResponse(200, { ok: true }))

      const cmd = new IAMServiceCommand({
        uri: 'http://iam.local/users',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(200)
      expect(fetchStub.calledTwice).to.be.true
    })

    it('should throw after exhausting retries', async () => {
      fetchStub.rejects(new Error('iam unavailable'))

      const cmd = new IAMServiceCommand({
        uri: 'http://iam.local/users',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
      })

      try {
        await cmd.execute()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('iam unavailable')
      }
    })

    it('should handle non-200 status codes without throwing', async () => {
      fetchStub.resolves(
        makeFetchResponse(403, { error: 'forbidden' }, 'Forbidden'),
      )

      const cmd = new IAMServiceCommand({
        uri: 'http://iam.local/users/1',
        method: HttpMethod.DELETE,
        headers: { authorization: 'Bearer tok' },
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(403)
      expect(result.msg).to.equal('Forbidden')
    })

    it('should sanitize headers and strip disallowed ones', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new IAMServiceCommand({
        uri: 'http://iam.local/users',
        method: HttpMethod.GET,
        headers: {
          authorization: 'Bearer test',
          'x-is-admin': 'true',
          'x-forwarded-for': '1.2.3.4',
          'content-type': 'application/json',
        },
      })

      await cmd.execute()
      const reqHeaders = fetchStub.firstCall.args[1].headers
      expect(reqHeaders).to.have.property('authorization')
      expect(reqHeaders).to.have.property('x-is-admin')
      expect(reqHeaders).to.have.property('content-type')
      expect(reqHeaders).not.to.have.property('x-forwarded-for')
    })
  })
})
