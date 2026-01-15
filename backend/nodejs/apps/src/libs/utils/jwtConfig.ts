import jwt from 'jsonwebtoken';

export interface JwtConfig {
  algorithm: jwt.Algorithm;
  useRSA: boolean;
}

/**
 * Get JWT configuration based on environment settings
 */
export function getJwtConfig(): JwtConfig {
  const encryptionKey = process.env.JWT_ENCRYPTION_KEY;
  const useRSA = encryptionKey === 'RS256';
  
  return {
    algorithm: useRSA ? 'RS256' : 'HS256',
    useRSA
  };
}

/**
 * Get the appropriate JWT key based on algorithm and key type
 * @param config - The app configuration
 * @param keyType - 'jwt' or 'scopedJwt'
 * @param isPrivate - true for private key (signing), false for public key (verification)
 */
export function getJwtKeyFromConfig(
  config: any,
  keyType: 'jwt' | 'scopedJwt',
  isPrivate: boolean = true
): string {
  const algorithm = config.jwtAlgorithm;
  
  if (algorithm === 'RS256') {
    if (keyType === 'jwt') {
      return isPrivate ? config.jwtPrivateKey : config.jwtPublicKey;
    } else {
      return isPrivate ? config.scopedJwtPrivateKey : config.scopedJwtPublicKey;
    }
  } else {
    // HS256
    return keyType === 'jwt' ? config.jwtSecret : config.scopedJwtSecret;
  }
}