import { injectable } from 'inversify'
import crypto from 'crypto'
import { Logger } from '../../../libs/services/logger.service'

/**
 * RSA Key Pair structure
 */
export interface RSAKeyPair {
  privateKey: string
  publicKey: string
  kid: string
}

/**
 * JWKS Key structure (RFC 7517)
 */
export interface JWK {
  kty: 'RSA'
  use: 'sig'
  alg: 'RS256'
  kid: string
  n: string
  e: string
}

/**
 * JWKS structure
 */
export interface JWKS {
  keys: JWK[]
}

/**
 * RSA Key Management Service
 *
 * Handles RSA key pair generation, storage, and JWKS formatting for OAuth2/OIDC.
 * Uses RS256 (RSA Signature with SHA-256) for JWT signing.
 *
 * Key storage options:
 * 1. Environment variable (OAUTH_RSA_PRIVATE_KEY) - recommended for production
 * 2. Auto-generated on startup (for development) - regenerates on restart
 *
 * @see https://datatracker.ietf.org/doc/html/rfc7517 - JSON Web Key (JWK)
 * @see https://datatracker.ietf.org/doc/html/rfc7518 - JSON Web Algorithms (JWA)
 */
@injectable()
export class RSAKeyService {
  private keyPair: RSAKeyPair
  private jwk: JWK
  private logger: Logger

  constructor(logger: Logger, privateKeyPem?: string) {
    this.logger = logger

    if (privateKeyPem && this.isValidRsaPrivateKeyPem(privateKeyPem)) {
      // Load existing key pair from provided PEM
      try {
        this.keyPair = this.loadKeyPair(privateKeyPem)
        this.logger.info('RSA key pair loaded from configuration')
      } catch (error) {
        this.logger.warn('Failed to load RSA key from configuration, generating new keys', {
          error: error instanceof Error ? error.message : 'Unknown error',
        })
        this.keyPair = this.generateKeyPair()
        this.logger.info('New RSA key pair generated')
      }
    } else {
      // Generate new key pair
      this.keyPair = this.generateKeyPair()
      this.logger.info('New RSA key pair generated for OAuth token signing')
    }

    // Generate JWK from public key
    this.jwk = this.publicKeyToJWK(this.keyPair.publicKey, this.keyPair.kid)
  }

  /**
   * Check if a string looks like a valid RSA private key PEM
   */
  private isValidRsaPrivateKeyPem(value: string): boolean {
    if (!value || typeof value !== 'string') {
      return false
    }
    // Check for PEM header markers
    const trimmed = value.trim()
    return (
      trimmed.includes('-----BEGIN') &&
      trimmed.includes('PRIVATE KEY-----') &&
      trimmed.includes('-----END')
    )
  }

  /**
   * Generate a new RSA key pair
   */
  private generateKeyPair(): RSAKeyPair {
    const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', {
      modulusLength: 2048,
      publicKeyEncoding: {
        type: 'spki',
        format: 'pem',
      },
      privateKeyEncoding: {
        type: 'pkcs8',
        format: 'pem',
      },
    })

    // Generate a key ID (kid) based on public key thumbprint
    const kid = this.generateKeyId(publicKey)

    return { privateKey, publicKey, kid }
  }

  /**
   * Load key pair from existing private key PEM
   */
  private loadKeyPair(privateKeyPem: string): RSAKeyPair {
    // Derive public key from private key
    const privateKeyObject = crypto.createPrivateKey(privateKeyPem)
    const publicKeyObject = crypto.createPublicKey(privateKeyObject)

    const publicKey = publicKeyObject.export({
      type: 'spki',
      format: 'pem',
    }) as string

    const kid = this.generateKeyId(publicKey)

    return {
      privateKey: privateKeyPem,
      publicKey,
      kid,
    }
  }

  /**
   * Generate a key ID (kid) from the public key
   * Uses SHA-256 thumbprint of the JWK per RFC 7638
   */
  private generateKeyId(publicKeyPem: string): string {
    const hash = crypto.createHash('sha256').update(publicKeyPem).digest()
    // Use first 8 bytes as hex for a short, unique identifier
    return hash.subarray(0, 8).toString('hex')
  }

  /**
   * Convert PEM public key to JWK format
   */
  private publicKeyToJWK(publicKeyPem: string, kid: string): JWK {
    const publicKeyObject = crypto.createPublicKey(publicKeyPem)

    // Export as JWK
    const jwkExport = publicKeyObject.export({ format: 'jwk' }) as {
      n: string
      e: string
    }

    return {
      kty: 'RSA',
      use: 'sig',
      alg: 'RS256',
      kid,
      n: jwkExport.n,
      e: jwkExport.e,
    }
  }

  /**
   * Get the private key for signing
   */
  getPrivateKey(): string {
    return this.keyPair.privateKey
  }

  /**
   * Get the public key for verification
   */
  getPublicKey(): string {
    return this.keyPair.publicKey
  }

  /**
   * Get the key ID
   */
  getKeyId(): string {
    return this.keyPair.kid
  }

  /**
   * Get the JWK representation of the public key
   */
  getJWK(): JWK {
    return this.jwk
  }

  /**
   * Get the JWKS (JSON Web Key Set) containing all public keys
   */
  getJWKS(): JWKS {
    return {
      keys: [this.jwk],
    }
  }

  /**
   * Export the private key PEM for persistence
   * This can be stored securely and provided via environment variable
   */
  exportPrivateKeyPem(): string {
    return this.keyPair.privateKey
  }
}
