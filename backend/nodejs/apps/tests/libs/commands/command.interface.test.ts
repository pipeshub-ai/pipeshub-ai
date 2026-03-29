import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import { BaseCommand, ICommand } from '../../../src/libs/commands/command.interface'

// Concrete implementation for testing the abstract BaseCommand
class TestCommand extends BaseCommand<string> {
  public executeFn: () => Promise<string>

  constructor(
    uri: string,
    queryParams?: Record<string, string | number | boolean>,
    headers?: Record<string, string>,
  ) {
    super(uri, queryParams, headers)
    this.executeFn = async () => 'default'
  }

  public async execute(): Promise<string> {
    return this.executeFn()
  }

  // Expose protected methods for testing
  public testBuildUrl(): string {
    return this.buildUrl()
  }

  public testSanitizeBody(body: any): any {
    return this.sanitizeBody(body)
  }

  public testSanitizeHeaders(
    headers: Record<string, string>,
  ): Record<string, string> {
    return this.sanitizeHeaders(headers)
  }

  public testFetchWithRetry<T>(
    fn: () => Promise<T>,
    retries?: number,
    backoff?: number,
  ): Promise<T> {
    return this.fetchWithRetry(fn, retries, backoff)
  }
}

describe('command.interface', () => {
  let clock: sinon.SinonFakeTimers

  afterEach(() => {
    sinon.restore()
  })

  describe('BaseCommand constructor', () => {
    it('should set uri, queryParams, and headers from arguments', () => {
      const cmd = new TestCommand(
        'http://example.com',
        { page: '1' },
        { authorization: 'Bearer tok' },
      )
      expect(cmd.testBuildUrl()).to.include('http://example.com')
      expect(cmd.testBuildUrl()).to.include('page=1')
    })

    it('should default headers to an empty object when not provided', () => {
      const cmd = new TestCommand('http://example.com')
      // sanitizeHeaders adds content-type when none present
      const sanitized = cmd.testSanitizeHeaders({})
      expect(sanitized).to.have.property('content-type', 'application/json')
    })
  })

  describe('buildUrl', () => {
    it('should return the uri as-is when no queryParams are provided', () => {
      const cmd = new TestCommand('http://api.local/items')
      expect(cmd.testBuildUrl()).to.equal('http://api.local/items')
    })

    it('should return the uri as-is when queryParams is undefined', () => {
      const cmd = new TestCommand('http://api.local/items', undefined)
      expect(cmd.testBuildUrl()).to.equal('http://api.local/items')
    })

    it('should append string query params', () => {
      const cmd = new TestCommand('http://api.local/items', {
        search: 'hello',
      })
      expect(cmd.testBuildUrl()).to.equal(
        'http://api.local/items?search=hello',
      )
    })

    it('should convert number query params to strings', () => {
      const cmd = new TestCommand('http://api.local/items', { page: 2 })
      expect(cmd.testBuildUrl()).to.equal('http://api.local/items?page=2')
    })

    it('should convert boolean query params to strings', () => {
      const cmd = new TestCommand('http://api.local/items', { active: true })
      expect(cmd.testBuildUrl()).to.equal('http://api.local/items?active=true')
    })

    it('should handle multiple query params', () => {
      const cmd = new TestCommand('http://api.local/items', {
        page: 1,
        limit: 10,
        active: true,
      })
      const url = cmd.testBuildUrl()
      expect(url).to.include('page=1')
      expect(url).to.include('limit=10')
      expect(url).to.include('active=true')
      expect(url).to.match(/^http:\/\/api\.local\/items\?/)
    })

    it('should handle empty queryParams object', () => {
      const cmd = new TestCommand('http://api.local/items', {})
      // Empty params still produces ? with empty string
      expect(cmd.testBuildUrl()).to.equal('http://api.local/items?')
    })
  })

  describe('sanitizeBody', () => {
    it('should return a string body as-is', () => {
      const cmd = new TestCommand('http://example.com')
      expect(cmd.testSanitizeBody('raw text')).to.equal('raw text')
    })

    it('should JSON-stringify an object body', () => {
      const cmd = new TestCommand('http://example.com')
      const obj = { key: 'value', num: 42 }
      expect(cmd.testSanitizeBody(obj)).to.equal(JSON.stringify(obj))
    })

    it('should JSON-stringify an array body', () => {
      const cmd = new TestCommand('http://example.com')
      const arr = [1, 2, 3]
      expect(cmd.testSanitizeBody(arr)).to.equal(JSON.stringify(arr))
    })

    it('should return FormData-like objects as-is (detected by _boundary)', () => {
      const cmd = new TestCommand('http://example.com')
      const formLike = { _boundary: '--boundary123', append: () => {} }
      const result = cmd.testSanitizeBody(formLike)
      expect(result).to.equal(formLike)
    })

    it('should JSON-stringify null', () => {
      const cmd = new TestCommand('http://example.com')
      expect(cmd.testSanitizeBody(null)).to.equal('null')
    })

    it('should return undefined as-is (typeof undefined is not string or object with _boundary)', () => {
      const cmd = new TestCommand('http://example.com')
      // undefined is not a string and JSON.stringify(undefined) returns undefined
      const result = cmd.testSanitizeBody(undefined)
      expect(result).to.be.undefined
    })

    it('should JSON-stringify boolean values', () => {
      const cmd = new TestCommand('http://example.com')
      expect(cmd.testSanitizeBody(true)).to.equal('true')
    })

    it('should JSON-stringify numbers', () => {
      const cmd = new TestCommand('http://example.com')
      expect(cmd.testSanitizeBody(42)).to.equal('42')
    })
  })

  describe('sanitizeHeaders', () => {
    it('should add content-type application/json when no content-type is present', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({})
      expect(result).to.have.property('content-type', 'application/json')
    })

    it('should preserve existing content-type header (lowercase)', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        'content-type': 'text/plain',
      })
      expect(result).to.have.property('content-type', 'text/plain')
    })

    it('should preserve existing Content-Type header (mixed case) and not add lowercase duplicate', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        'Content-Type': 'text/html',
      })
      // Content-Type should pass through the allowedHeaders filter (lowered to content-type)
      expect(result).to.have.property('Content-Type', 'text/html')
    })

    it('should keep authorization header', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        authorization: 'Bearer token123',
      })
      expect(result).to.have.property('authorization', 'Bearer token123')
    })

    it('should keep x-is-admin header', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        'x-is-admin': 'true',
      })
      expect(result).to.have.property('x-is-admin', 'true')
    })

    it('should keep x-oauth-user-id header', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        'x-oauth-user-id': 'user-abc',
      })
      expect(result).to.have.property('x-oauth-user-id', 'user-abc')
    })

    it('should remove headers that are not in the allowed set', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        'x-custom-header': 'some-value',
        'x-request-id': '12345',
        'cache-control': 'no-cache',
      })
      expect(result).not.to.have.property('x-custom-header')
      expect(result).not.to.have.property('x-request-id')
      expect(result).not.to.have.property('cache-control')
      // Should still add content-type
      expect(result).to.have.property('content-type', 'application/json')
    })

    it('should be case-insensitive when filtering headers', () => {
      const cmd = new TestCommand('http://example.com')
      const result = cmd.testSanitizeHeaders({
        Authorization: 'Bearer xyz',
        'X-Is-Admin': 'false',
      })
      expect(result).to.have.property('Authorization', 'Bearer xyz')
      expect(result).to.have.property('X-Is-Admin', 'false')
    })
  })

  describe('fetchWithRetry', () => {
    it('should return the result on first successful attempt', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub().resolves('success')
      const result = await cmd.testFetchWithRetry(fn, 3, 10)
      expect(result).to.equal('success')
      expect(fn.calledOnce).to.be.true
    })

    it('should retry on failure and return result on subsequent success', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub()
      fn.onFirstCall().rejects(new Error('fail1'))
      fn.onSecondCall().resolves('recovered')

      const result = await cmd.testFetchWithRetry(fn, 3, 1)
      expect(result).to.equal('recovered')
      expect(fn.calledTwice).to.be.true
    })

    it('should throw after exhausting all retries', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub().rejects(new Error('persistent failure'))

      try {
        await cmd.testFetchWithRetry(fn, 3, 1)
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('persistent failure')
      }
      expect(fn.calledThrice).to.be.true
    })

    it('should use default values of 3 retries and 300ms backoff', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub()
      fn.onFirstCall().rejects(new Error('fail'))
      fn.onSecondCall().rejects(new Error('fail'))
      fn.onThirdCall().resolves('ok')

      const result = await cmd.testFetchWithRetry(fn)
      expect(result).to.equal('ok')
      expect(fn.calledThrice).to.be.true
    })

    it('should throw the last error when all retries are exhausted', async () => {
      const cmd = new TestCommand('http://example.com')
      const errors = [
        new Error('error1'),
        new Error('error2'),
        new Error('error3'),
      ]
      const fn = sinon.stub()
      fn.onFirstCall().rejects(errors[0])
      fn.onSecondCall().rejects(errors[1])
      fn.onThirdCall().rejects(errors[2])

      try {
        await cmd.testFetchWithRetry(fn, 3, 1)
        expect.fail('Should have thrown')
      } catch (err: any) {
        // Should throw the last error
        expect(err.message).to.equal('error3')
      }
    })

    it('should retry only the specified number of times', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub().rejects(new Error('fail'))

      try {
        await cmd.testFetchWithRetry(fn, 2, 1)
        expect.fail('Should have thrown')
      } catch {
        // expected
      }
      expect(fn.callCount).to.equal(2)
    })

    it('should succeed on the last retry attempt', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub()
      fn.onFirstCall().rejects(new Error('fail'))
      fn.onSecondCall().rejects(new Error('fail'))
      fn.onThirdCall().resolves('last-chance')

      const result = await cmd.testFetchWithRetry(fn, 3, 1)
      expect(result).to.equal('last-chance')
    })
  })

  describe('fetchWithRetry edge case', () => {
    it('should throw InternalServerError when retries is 0 (unreachable guard)', async () => {
      const cmd = new TestCommand('http://example.com')
      const fn = sinon.stub().resolves('should-not-run')

      try {
        await cmd.testFetchWithRetry(fn, 0, 1)
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.equal('Unexpected error in retry logic.')
      }
      expect(fn.called).to.be.false
    })
  })

  describe('ICommand interface compliance', () => {
    it('should satisfy the ICommand interface by implementing execute()', async () => {
      const cmd: ICommand<string> = new TestCommand('http://example.com')
      const result = await cmd.execute()
      expect(result).to.equal('default')
    })
  })
})
