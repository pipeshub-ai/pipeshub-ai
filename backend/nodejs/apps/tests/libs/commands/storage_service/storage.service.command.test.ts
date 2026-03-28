import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  StorageServiceCommand,
  StorageCommandOptions,
} from '../../../../src/libs/commands/storage_service/storage.service.command'
import { HttpMethod } from '../../../../src/libs/enums/http-methods.enum'

describe('StorageServiceCommand', () => {
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
      const cmd = new StorageServiceCommand({
        uri: 'http://storage.local/files',
        method: HttpMethod.POST,
        headers: { authorization: 'Bearer tok' },
        queryParams: { bucket: 'main' },
        body: { file: 'data' },
      })
      expect(cmd).to.be.instanceOf(StorageServiceCommand)
    })

    it('should create an instance with minimal options', () => {
      const cmd = new StorageServiceCommand({
        uri: 'http://storage.local/files',
        method: HttpMethod.GET,
        headers: {},
      })
      expect(cmd).to.be.instanceOf(StorageServiceCommand)
    })
  })

  describe('execute', () => {
    it('should make a fetch call and return parsed JSON data', async () => {
      const responseBody = { url: 'http://storage.local/files/abc.pdf' }
      fetchStub.resolves(makeFetchResponse(200, responseBody))

      const cmd = new StorageServiceCommand({
        uri: 'http://storage.local/files',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
      })

      const result = await cmd.execute()
      // StorageServiceCommand returns the parsed JSON directly (response.json())
      expect(result).to.deep.equal(responseBody)
    })

    it('should build URL with query params', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new StorageServiceCommand({
        uri: 'http://storage.local/files',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
        queryParams: { path: '/uploads', recursive: true },
      })

      await cmd.execute()
      const url = fetchStub.firstCall.args[0]
      expect(url).to.include('path=%2Fuploads')
      expect(url).to.include('recursive=true')
    })

    it('should include body in request options when body is provided', async () => {
      fetchStub.resolves(makeFetchResponse(200, { stored: true }))

      const cmd = new StorageServiceCommand({
        uri: 'http://storage.local/files',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { filename: 'doc.pdf' },
      })

      await cmd.execute()
      const reqOptions = fetchStub.firstCall.args[1]
      expect(reqOptions.body).to.equal(
        JSON.stringify({ filename: 'doc.pdf' }),
      )
    })

    it('should not include body in request options when body is undefined', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new StorageServiceCommand({
        uri: 'http://storage.local/files',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
      })

      await cmd.execute()
      const reqOptions = fetchStub.firstCall.args[1]
      expect(reqOptions.body).to.be.undefined
    })

    it('should use the correct HTTP method', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      for (const method of [HttpMethod.GET, HttpMethod.PUT, HttpMethod.DELETE]) {
        fetchStub.resetHistory()
        const cmd = new StorageServiceCommand({
          uri: 'http://storage.local/files',
          method,
          headers: { 'content-type': 'application/json' },
        })

        await cmd.execute()
        expect(fetchStub.firstCall.args[1].method).to.equal(method)
      }
    })

    it('should retry on failure and succeed on retry', async () => {
      fetchStub.onFirstCall().rejects(new Error('timeout'))
      fetchStub
        .onSecondCall()
        .resolves(makeFetchResponse(200, { success: true }))

      const cmd = new StorageServiceCommand({
        uri: 'http://storage.local/files',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
      })

      const result = await cmd.execute()
      expect(result).to.deep.equal({ success: true })
      expect(fetchStub.calledTwice).to.be.true
    })

    it('should throw after exhausting retries', async () => {
      fetchStub.rejects(new Error('storage unavailable'))

      const cmd = new StorageServiceCommand({
        uri: 'http://storage.local/files',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
      })

      try {
        await cmd.execute()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('storage unavailable')
      }
    })

    it('should sanitize headers and strip disallowed ones', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new StorageServiceCommand({
        uri: 'http://storage.local/files',
        method: HttpMethod.GET,
        headers: {
          authorization: 'Bearer test',
          'x-custom-header': 'disallowed',
          'content-type': 'application/json',
        },
      })

      await cmd.execute()
      const reqHeaders = fetchStub.firstCall.args[1].headers
      expect(reqHeaders).to.have.property('authorization')
      expect(reqHeaders).to.have.property('content-type')
      expect(reqHeaders).not.to.have.property('x-custom-header')
    })
  })
})
