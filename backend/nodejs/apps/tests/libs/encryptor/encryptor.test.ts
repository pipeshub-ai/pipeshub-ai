import 'reflect-metadata'
import { expect } from 'chai'
import sinon from 'sinon'
import crypto from 'crypto'
import { EncryptionService } from '../../../src/libs/encryptor/encryptor'

describe('EncryptionService', () => {
  // Generate a valid 256-bit key for AES-256-GCM
  const algorithm = 'aes-256-gcm'
  const secretKey = crypto.randomBytes(32).toString('hex')

  beforeEach(() => {
    // Reset the singleton instance before each test to avoid cross-test state
    ;(EncryptionService as any).instance = undefined
  })

  afterEach(() => {
    sinon.restore()
    ;(EncryptionService as any).instance = undefined
  })

  describe('getInstance', () => {
    it('should return an EncryptionService instance', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      expect(service).to.be.instanceOf(EncryptionService)
    })

    it('should return the same instance on subsequent calls (singleton)', () => {
      const service1 = EncryptionService.getInstance(algorithm, secretKey)
      const service2 = EncryptionService.getInstance(algorithm, secretKey)
      expect(service1).to.equal(service2)
    })
  })

  describe('encrypt', () => {
    it('should return an encrypted string in iv:ciphertext:authTag format', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const encrypted = service.encrypt('hello world')
      const parts = encrypted.split(':')
      expect(parts).to.have.length(3)
      // Each part should be a hex string
      for (const part of parts) {
        expect(part).to.match(/^[0-9a-f]+$/i)
      }
    })

    it('should produce different encrypted output for the same input (due to random IV)', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const enc1 = service.encrypt('same text')
      const enc2 = service.encrypt('same text')
      expect(enc1).not.to.equal(enc2)
    })

    it('should handle empty string', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const encrypted = service.encrypt('')
      expect(encrypted).to.be.a('string')
      expect(encrypted.split(':')).to.have.length(3)
    })

    it('should handle long strings', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const longText = 'a'.repeat(10000)
      const encrypted = service.encrypt(longText)
      expect(encrypted).to.be.a('string')
      expect(encrypted.split(':')).to.have.length(3)
    })

    it('should handle special characters', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const specialText = 'Hello! @#$%^&*() \n\t unicode: \u00e9\u00e8\u00ea'
      const encrypted = service.encrypt(specialText)
      expect(encrypted).to.be.a('string')
    })

    it('should use 12-byte IV (24 hex characters)', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const encrypted = service.encrypt('test')
      const iv = encrypted.split(':')[0]
      expect(iv).to.have.length(24) // 12 bytes = 24 hex chars
    })

    it('should throw EncryptionError when crypto.randomBytes fails', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const originalRandomBytes = crypto.randomBytes
      ;(crypto as any).randomBytes = () => { throw new Error('entropy failure') }
      try {
        expect(() => service.encrypt('test')).to.throw('entropy failure')
      } finally {
        ;(crypto as any).randomBytes = originalRandomBytes
      }
    })
  })

  describe('decrypt', () => {
    it('should decrypt encrypted text back to original', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const original = 'hello world'
      const encrypted = service.encrypt(original)
      const decrypted = service.decrypt(encrypted)
      expect(decrypted).to.equal(original)
    })

    it('should correctly round-trip empty strings', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const encrypted = service.encrypt('')
      const decrypted = service.decrypt(encrypted)
      expect(decrypted).to.equal('')
    })

    it('should correctly round-trip special characters', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const original = 'Unicode: \u00e9\u00e8 \nNewline \tTab'
      const encrypted = service.encrypt(original)
      const decrypted = service.decrypt(encrypted)
      expect(decrypted).to.equal(original)
    })

    it('should correctly round-trip long strings', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const original = 'x'.repeat(10000)
      const encrypted = service.encrypt(original)
      const decrypted = service.decrypt(encrypted)
      expect(decrypted).to.equal(original)
    })

    it('should throw DecryptionError for null input', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      try {
        service.decrypt(null as any)
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.include('Decryption failed')
        expect(err.message).to.include('null or undefined')
      }
    })

    it('should throw DecryptionError for undefined input', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      try {
        service.decrypt(undefined as any)
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.include('Decryption failed')
        expect(err.message).to.include('null or undefined')
      }
    })

    it('should throw for invalid format (missing authTag part)', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      try {
        service.decrypt('aabbcc:ddeeff')
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.include('Decryption failed')
      }
    })

    it('should throw for invalid format (single value)', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      try {
        service.decrypt('notvalidencrypted')
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.include('Decryption failed')
      }
    })

    it('should throw for tampered ciphertext', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const encrypted = service.encrypt('sensitive data')
      const parts = encrypted.split(':')
      // Tamper with the ciphertext
      parts[1] = 'ff' + parts[1]!.substring(2)
      const tampered = parts.join(':')
      try {
        service.decrypt(tampered)
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.include('Decryption failed')
      }
    })

    it('should throw for tampered authTag', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const encrypted = service.encrypt('sensitive data')
      const parts = encrypted.split(':')
      // Tamper with the auth tag
      parts[2] = 'ff' + parts[2]!.substring(2)
      const tampered = parts.join(':')
      try {
        service.decrypt(tampered)
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.include('Decryption failed')
      }
    })

    it('should throw when using a different secret key', () => {
      const service1 = EncryptionService.getInstance(algorithm, secretKey)
      const encrypted = service1.encrypt('secret message')

      // Reset singleton and create with different key
      ;(EncryptionService as any).instance = undefined
      const differentKey = crypto.randomBytes(32).toString('hex')
      const service2 = EncryptionService.getInstance(algorithm, differentKey)

      try {
        service2.decrypt(encrypted)
        expect.fail('Should have thrown')
      } catch (err: any) {
        expect(err.message).to.include('Decryption failed')
      }
    })
  })

  describe('encrypt and decrypt integration', () => {
    it('should handle JSON data round-trip', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const original = JSON.stringify({ key: 'value', num: 42, arr: [1, 2] })
      const encrypted = service.encrypt(original)
      const decrypted = service.decrypt(encrypted)
      expect(JSON.parse(decrypted)).to.deep.equal(JSON.parse(original))
    })

    it('should handle multiple encrypt/decrypt cycles', () => {
      const service = EncryptionService.getInstance(algorithm, secretKey)
      const texts = [
        'first message',
        'second message',
        'third message with numbers 123',
      ]

      for (const text of texts) {
        const encrypted = service.encrypt(text)
        const decrypted = service.decrypt(encrypted)
        expect(decrypted).to.equal(text)
      }
    })
  })
})
