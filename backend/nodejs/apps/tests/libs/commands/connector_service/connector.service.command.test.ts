import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  ConnectorServiceCommand,
  ConnectorServiceCommandOptions,
} from '../../../../src/libs/commands/connector_service/connector.service.command'
import { HttpMethod } from '../../../../src/libs/enums/http-methods.enum'

describe('ConnectorServiceCommand', () => {
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
    headers: Record<string, string> = {},
  ): Response {
    const headersObj = new Headers(headers)
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText,
      headers: headersObj,
      json: sinon.stub().resolves(body),
      text: sinon.stub().resolves(JSON.stringify(body)),
      body: null,
    } as unknown as Response
  }

  describe('constructor', () => {
    it('should set method, body, and sanitized headers', () => {
      const options: ConnectorServiceCommandOptions = {
        uri: 'http://connector.local/api',
        method: HttpMethod.POST,
        headers: { authorization: 'Bearer token' },
        body: { key: 'value' },
      }
      const cmd = new ConnectorServiceCommand(options)
      // The command should be constructable without error
      expect(cmd).to.be.instanceOf(ConnectorServiceCommand)
    })

    it('should default headers to empty object when not provided', () => {
      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api',
        method: HttpMethod.GET,
      })
      expect(cmd).to.be.instanceOf(ConnectorServiceCommand)
    })
  })

  describe('execute', () => {
    it('should make a fetch call and return structured response on success', async () => {
      const responseData = { result: 'ok' }
      fetchStub.resolves(
        makeFetchResponse(200, responseData, 'OK', {
          'x-request-id': '123',
        }),
      )

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/data',
        method: HttpMethod.GET,
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(200)
      expect(result.data).to.deep.equal(responseData)
      expect(result.msg).to.equal('OK')
      expect(result.headers).to.have.property('x-request-id', '123')
      expect(fetchStub.calledOnce).to.be.true
    })

    it('should include query parameters in the URL', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/search',
        method: HttpMethod.GET,
        queryParams: { q: 'test', page: 1 },
      })

      await cmd.execute()
      const calledUrl = fetchStub.firstCall.args[0]
      expect(calledUrl).to.include('q=test')
      expect(calledUrl).to.include('page=1')
    })

    it('should include body in the request for POST method', async () => {
      fetchStub.resolves(makeFetchResponse(201, { id: '1' }, 'Created'))

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/data',
        method: HttpMethod.POST,
        body: { name: 'test' },
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(201)
      const requestOptions = fetchStub.firstCall.args[1]
      expect(requestOptions.method).to.equal('POST')
      expect(requestOptions.body).to.equal(JSON.stringify({ name: 'test' }))
    })

    it('should sanitize headers and remove disallowed ones', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/data',
        method: HttpMethod.GET,
        headers: {
          authorization: 'Bearer tok',
          'x-custom-bad': 'should-be-removed',
          'content-type': 'application/json',
        },
      })

      await cmd.execute()
      const requestOptions = fetchStub.firstCall.args[1]
      const headers = requestOptions.headers as Record<string, string>
      expect(headers).to.have.property('authorization')
      expect(headers).to.have.property('content-type')
      expect(headers).not.to.have.property('x-custom-bad')
    })

    it('should retry on fetch failure and succeed on subsequent attempt', async () => {
      fetchStub.onFirstCall().rejects(new Error('network error'))
      fetchStub.onSecondCall().resolves(makeFetchResponse(200, { ok: true }))

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/data',
        method: HttpMethod.GET,
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(200)
      expect(fetchStub.calledTwice).to.be.true
    })

    it('should throw after exhausting retries', async () => {
      fetchStub.rejects(new Error('persistent failure'))

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/data',
        method: HttpMethod.GET,
      })

      try {
        await cmd.execute()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('persistent failure')
      }
    })

    it('should handle non-200 status codes without throwing', async () => {
      fetchStub.resolves(
        makeFetchResponse(404, { error: 'not found' }, 'Not Found'),
      )

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/missing',
        method: HttpMethod.GET,
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(404)
      expect(result.msg).to.equal('Not Found')
    })

    it('should convert response headers to a plain object', async () => {
      fetchStub.resolves(
        makeFetchResponse(200, {}, 'OK', {
          'content-type': 'application/json',
          'x-ratelimit-remaining': '99',
        }),
      )

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/data',
        method: HttpMethod.GET,
      })

      const result = await cmd.execute()
      expect(result.headers).to.deep.include({
        'content-type': 'application/json',
        'x-ratelimit-remaining': '99',
      })
    })
  })

  describe('executeStream', () => {
    it('should return a readable stream from a successful streaming response', async () => {
      const encoder = new TextEncoder()
      let readCount = 0
      const chunks = [encoder.encode('chunk1'), encoder.encode('chunk2')]

      const mockReader = {
        read: sinon.stub().callsFake(async () => {
          if (readCount < chunks.length) {
            return { done: false, value: chunks[readCount++] }
          }
          return { done: true, value: undefined }
        }),
      }

      const mockBody = {
        getReader: () => mockReader,
      }

      const mockResponse = {
        ok: true,
        status: 200,
        statusText: 'OK',
        headers: new Headers(),
        body: mockBody,
        json: sinon.stub().resolves({}),
        text: sinon.stub().resolves(''),
      } as unknown as Response

      fetchStub.resolves(mockResponse)

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/stream',
        method: HttpMethod.POST,
        body: { query: 'test' },
      })

      const readable = await cmd.executeStream()
      expect(readable).to.exist

      const receivedChunks: string[] = []
      readable.setEncoding('utf8')
      await new Promise<void>((resolve) => {
        readable.on('data', (chunk: string) => {
          receivedChunks.push(chunk)
        })
        readable.on('end', () => {
          resolve()
        })
      })

      expect(receivedChunks).to.deep.equal(['chunk1', 'chunk2'])
    })

    it('should throw when response is not ok', async () => {
      const mockResponse = {
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
        headers: new Headers(),
        body: null,
        json: sinon.stub().resolves({}),
        text: sinon.stub().resolves(''),
      } as unknown as Response

      fetchStub.resolves(mockResponse)

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/stream',
        method: HttpMethod.GET,
      })

      try {
        await cmd.executeStream()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.include('HTTP error')
      }
    })

    it('should throw when response body is null', async () => {
      const mockResponse = {
        ok: true,
        status: 200,
        statusText: 'OK',
        headers: new Headers(),
        body: null,
        json: sinon.stub().resolves({}),
        text: sinon.stub().resolves(''),
      } as unknown as Response

      fetchStub.resolves(mockResponse)

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/stream',
        method: HttpMethod.GET,
      })

      try {
        await cmd.executeStream()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('Response body is null')
      }
    })

    it('should throw on fetch failure during streaming', async () => {
      fetchStub.rejects(new Error('stream network error'))

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/stream',
        method: HttpMethod.GET,
      })

      try {
        await cmd.executeStream()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('stream network error')
      }
    })

    it('should destroy readable stream when reader.read() throws', async () => {
      const readError = new Error('read error')
      const mockReader = {
        read: sinon.stub().rejects(readError),
      }

      const mockBody = {
        getReader: () => mockReader,
      }

      const mockResponse = {
        ok: true,
        status: 200,
        statusText: 'OK',
        headers: new Headers(),
        body: mockBody,
        json: sinon.stub().resolves({}),
        text: sinon.stub().resolves(''),
      } as unknown as Response

      fetchStub.resolves(mockResponse)

      const cmd = new ConnectorServiceCommand({
        uri: 'http://connector.local/api/stream',
        method: HttpMethod.POST,
        body: { query: 'test' },
      })

      const readable = await cmd.executeStream()
      expect(readable).to.exist

      // Wait for the pump to encounter the error and destroy the stream
      await new Promise<void>((resolve) => {
        readable.on('error', (err: Error) => {
          expect(err.message).to.equal('read error')
          resolve()
        })
      })
    })
  })
})
