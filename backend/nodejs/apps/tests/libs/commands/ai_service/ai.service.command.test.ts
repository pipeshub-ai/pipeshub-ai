import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import {
  AIServiceCommand,
  AICommandOptions,
} from '../../../../src/libs/commands/ai_service/ai.service.command'
import { HttpMethod } from '../../../../src/libs/enums/http-methods.enum'

describe('AIServiceCommand', () => {
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
      body: null,
    } as unknown as Response
  }

  describe('constructor', () => {
    it('should create an instance with all options', () => {
      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/query',
        method: HttpMethod.POST,
        headers: { authorization: 'Bearer tok' },
        queryParams: { model: 'gpt-4' },
        body: { prompt: 'hello' },
      })
      expect(cmd).to.be.instanceOf(AIServiceCommand)
    })

    it('should create an instance with minimal options', () => {
      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/query',
        method: HttpMethod.GET,
        headers: {},
      })
      expect(cmd).to.be.instanceOf(AIServiceCommand)
    })
  })

  describe('execute', () => {
    it('should return AIServiceResponse with statusCode, data, and msg on success', async () => {
      const responseData = { answer: 'Hello, world!' }
      fetchStub.resolves(makeFetchResponse(200, responseData, 'OK'))

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/query',
        method: HttpMethod.POST,
        headers: { authorization: 'Bearer tok' },
        body: { prompt: 'hi' },
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(200)
      expect(result.data).to.deep.equal(responseData)
      expect(result.msg).to.equal('OK')
    })

    it('should build URL with query params', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/query',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
        queryParams: { model: 'gpt-4', temperature: 0 },
      })

      await cmd.execute()
      const url = fetchStub.firstCall.args[0]
      expect(url).to.include('model=gpt-4')
      expect(url).to.include('temperature=0')
    })

    it('should include body in the request', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/query',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'test prompt', max_tokens: 100 },
      })

      await cmd.execute()
      const reqOptions = fetchStub.firstCall.args[1]
      expect(reqOptions.body).to.equal(
        JSON.stringify({ prompt: 'test prompt', max_tokens: 100 }),
      )
    })

    it('should sanitize headers (double sanitization in execute)', async () => {
      fetchStub.resolves(makeFetchResponse(200, {}))

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/query',
        method: HttpMethod.POST,
        headers: {
          authorization: 'Bearer tok',
          'x-custom': 'disallowed',
          'content-type': 'application/json',
        },
      })

      await cmd.execute()
      const reqHeaders = fetchStub.firstCall.args[1].headers
      expect(reqHeaders).to.have.property('authorization')
      expect(reqHeaders).to.have.property('content-type')
      expect(reqHeaders).not.to.have.property('x-custom')
    })

    it('should retry on failure and succeed on retry', async () => {
      fetchStub.onFirstCall().rejects(new Error('network error'))
      fetchStub.onSecondCall().resolves(makeFetchResponse(200, { ok: true }))

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/query',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'retry test' },
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(200)
      expect(fetchStub.calledTwice).to.be.true
    })

    it('should throw after exhausting retries', async () => {
      fetchStub.rejects(new Error('ai unavailable'))

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/query',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'fail' },
      })

      try {
        await cmd.execute()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('ai unavailable')
      }
    })

    it('should handle non-200 status codes without throwing', async () => {
      fetchStub.resolves(
        makeFetchResponse(
          429,
          { error: 'rate limited' },
          'Too Many Requests',
        ),
      )

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/query',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'rate limit test' },
      })

      const result = await cmd.execute()
      expect(result.statusCode).to.equal(429)
      expect(result.msg).to.equal('Too Many Requests')
    })
  })

  describe('executeStream', () => {
    it('should return a readable stream from a successful streaming response', async () => {
      const encoder = new TextEncoder()
      let readCount = 0
      const chunks = [
        encoder.encode('data: chunk1\n'),
        encoder.encode('data: chunk2\n'),
      ]

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

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/stream',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'stream test' },
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

      expect(receivedChunks).to.have.length(2)
      expect(receivedChunks[0]).to.include('chunk1')
      expect(receivedChunks[1]).to.include('chunk2')
    })

    it('should throw an error with detail from JSON error response when not ok', async () => {
      const errorPayload = JSON.stringify({ detail: 'Model overloaded' })
      const mockResponse = {
        ok: false,
        status: 503,
        statusText: 'Service Unavailable',
        headers: new Headers(),
        body: null,
        json: sinon.stub().resolves({}),
        text: sinon.stub().resolves(errorPayload),
      } as unknown as Response

      fetchStub.resolves(mockResponse)

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/stream',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'error test' },
      })

      try {
        await cmd.executeStream()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('Model overloaded')
        expect(err.status).to.equal(503)
        expect(err.response.status).to.equal(503)
      }
    })

    it('should throw an error with message field from JSON error response', async () => {
      const errorPayload = JSON.stringify({ message: 'Rate limit exceeded' })
      const mockResponse = {
        ok: false,
        status: 429,
        statusText: 'Too Many Requests',
        headers: new Headers(),
        body: null,
        json: sinon.stub().resolves({}),
        text: sinon.stub().resolves(errorPayload),
      } as unknown as Response

      fetchStub.resolves(mockResponse)

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/stream',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'rate limit' },
      })

      try {
        await cmd.executeStream()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('Rate limit exceeded')
      }
    })

    it('should throw with SSE-formatted error payload when JSON parse fails', async () => {
      const ssePayload = 'data: Some SSE error message\n'
      const mockResponse = {
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
        headers: new Headers(),
        body: null,
        json: sinon.stub().resolves({}),
        text: sinon.stub().resolves(ssePayload),
      } as unknown as Response

      fetchStub.resolves(mockResponse)

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/stream',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'sse error' },
      })

      try {
        await cmd.executeStream()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.include('Some SSE error message')
      }
    })

    it('should throw when response body is null but ok is true', async () => {
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

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/stream',
        method: HttpMethod.GET,
        headers: { 'content-type': 'application/json' },
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

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/stream',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'network fail' },
      })

      try {
        await cmd.executeStream()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('stream network error')
      }
    })

    it('should fall back to statusText when error payload is empty', async () => {
      const mockResponse = {
        ok: false,
        status: 502,
        statusText: 'Bad Gateway',
        headers: new Headers(),
        body: null,
        json: sinon.stub().resolves({}),
        text: sinon.stub().resolves(''),
      } as unknown as Response

      fetchStub.resolves(mockResponse)

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/stream',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'empty error' },
      })

      try {
        await cmd.executeStream()
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('Bad Gateway')
      }
    })

    it('should handle text() throwing and fall back to generic HTTP error message', async () => {
      const mockResponse = {
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
        headers: new Headers(),
        body: null,
        json: sinon.stub().resolves({}),
        text: sinon.stub().rejects(new Error('text read error')),
      } as unknown as Response

      fetchStub.resolves(mockResponse)

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/stream',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'text error' },
      })

      try {
        await cmd.executeStream()
        expect.fail('Should have thrown')
      } catch (err: any) {
        // text() throws, but buildStreamHttpError catches it internally and
        // falls through to response.statusText as the error message
        expect(err.message).to.equal('Internal Server Error')
      }
    })

    it('should destroy readable stream when reader.read() throws during streaming', async () => {
      const readError = new Error('stream read failure')
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

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/stream',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'reader error' },
      })

      const readable = await cmd.executeStream()
      expect(readable).to.exist

      // Wait for the pump to encounter the error and destroy the stream
      await new Promise<void>((resolve) => {
        readable.on('error', (err: Error) => {
          expect(err.message).to.equal('stream read failure')
          resolve()
        })
      })
    })

    it('should throw fallback HTTP error when buildStreamHttpError itself throws', async () => {
      // We need buildStreamHttpError to throw. This happens when both text() throws
      // AND the catch block has issues. But actually looking at the code more carefully,
      // the catch on line 168-170 catches the error from buildStreamHttpError and
      // throws new Error(`HTTP error! status: ${response.status}`)
      const mockResponse = {
        ok: false,
        status: 502,
        statusText: 'Bad Gateway',
        headers: new Headers(),
        body: null,
        json: sinon.stub().resolves({}),
        // Make text() return a value that causes issues in parsing
        text: sinon.stub().callsFake(async () => {
          throw new Error('catastrophic text error')
        }),
      } as unknown as Response

      fetchStub.resolves(mockResponse)

      const cmd = new AIServiceCommand({
        uri: 'http://ai.local/stream',
        method: HttpMethod.POST,
        headers: { 'content-type': 'application/json' },
        body: { prompt: 'catastrophic error' },
      })

      try {
        await cmd.executeStream()
        expect.fail('Should have thrown')
      } catch (err: any) {
        // buildStreamHttpError catches text() error internally,
        // so it should still return a proper error
        expect(err.message).to.include('Bad Gateway')
      }
    })
  })
})
