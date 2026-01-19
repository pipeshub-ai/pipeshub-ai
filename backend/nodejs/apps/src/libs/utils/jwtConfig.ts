import { Algorithm, Secret } from 'jsonwebtoken'
import { ConfigService } from '../../modules/tokens_manager/services/cm.service'
import { RSAKeyService } from '../../modules/oauth_provider/services/rsa_key.service'
import { Logger } from '../services/logger.service'

export type JwtAlgorithm = 'HS256' | 'RS256'

export interface JwtConfig {
  algorithm: JwtAlgorithm
  secret?: string
  privateKey?: string
  publicKey?: string
  keyId?: string
}

export interface JwtKeyConfig {
  algorithm: Algorithm
  signingKey: Secret
  verifyKey: Secret
  keyId?: string
}

/**
 * Get JWT configuration from the platform.
 * Returns the algorithm and associated keys/secrets for JWT signing.
 * Defaults to HS256 with the platform JWT secret.
 */
export async function getJwtConfig(logger: Logger): Promise<JwtConfig> {
  const configService = ConfigService.getInstance()
  const jwtSecret = await configService.getJwtSecret()

  // Check environment for algorithm preference
  const algorithm = (process.env.JWT_ALGORITHM as JwtAlgorithm) || 'HS256'

  if (algorithm === 'RS256') {
    const rsaPrivateKey = process.env.OAUTH_RSA_PRIVATE_KEY
    const rsaKeyService = new RSAKeyService(logger, rsaPrivateKey)

    return {
      algorithm: 'RS256',
      privateKey: rsaKeyService.getPrivateKey(),
      publicKey: rsaKeyService.getPublicKey(),
      keyId: rsaKeyService.getKeyId(),
    }
  }

  return {
    algorithm: 'HS256',
    secret: jwtSecret,
  }
}

/**
 * Extract signing and verification keys from JwtConfig.
 * Returns the appropriate keys based on the algorithm.
 */
export function getJwtKeyFromConfig(config: JwtConfig): JwtKeyConfig {
  if (config.algorithm === 'RS256') {
    if (!config.privateKey || !config.publicKey) {
      throw new Error('RS256 algorithm requires privateKey and publicKey')
    }
    return {
      algorithm: 'RS256',
      signingKey: config.privateKey,
      verifyKey: config.publicKey,
      keyId: config.keyId,
    }
  }

  if (!config.secret) {
    throw new Error('HS256 algorithm requires secret')
  }
  return {
    algorithm: 'HS256',
    signingKey: config.secret,
    verifyKey: config.secret,
  }
}
