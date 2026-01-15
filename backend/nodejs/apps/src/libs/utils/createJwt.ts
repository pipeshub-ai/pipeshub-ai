import jwt from 'jsonwebtoken';
import { TokenScopes } from '../enums/token-scopes.enum';
import { getJwtConfig } from './jwtConfig';

export const mailJwtGenerator = (email: string, scopedJwtKey: string) => {
  const config = getJwtConfig();
  return jwt.sign(
    { email: email, scopes: [TokenScopes.SEND_MAIL] },
    scopedJwtKey,
    {
      algorithm: config.algorithm,
      expiresIn: '1h',
    },
  );
};

export const jwtGeneratorForForgotPasswordLink = (
  userEmail: string,
  userId: string,
  orgId: string,
  scopedJwtKey: string,
) => {
  const config = getJwtConfig();
  // Token for password reset
  const passwordResetToken = jwt.sign(
    {
      userEmail,
      userId,
      orgId,
      scopes: [TokenScopes.PASSWORD_RESET],
    },
    scopedJwtKey,
    { 
      algorithm: config.algorithm,
      expiresIn: '20m' 
    },
  );
  const mailAuthToken = jwt.sign(
    {
      userEmail,
      userId,
      orgId,
      scopes: [TokenScopes.SEND_MAIL],
    },
    scopedJwtKey,
    { 
      algorithm: config.algorithm,
      expiresIn: '1h' 
    },
  );

  return { passwordResetToken, mailAuthToken };
};

export const jwtGeneratorForNewAccountPassword = (
  userEmail: string,
  userId: string,
  orgId: string,
  scopedJwtKey: string,
) => {
  const config = getJwtConfig();
  // Token for password reset
  const passwordResetToken = jwt.sign(
    {
      userEmail,
      userId,
      orgId,
      scopes: [TokenScopes.PASSWORD_RESET],
    },
    scopedJwtKey,
    { 
      algorithm: config.algorithm,
      expiresIn: '48h' 
    },
  );
  const mailAuthToken = jwt.sign(
    {
      userEmail,
      userId,
      orgId,
      scopes: [TokenScopes.SEND_MAIL],
    },
    scopedJwtKey,
    { 
      algorithm: config.algorithm,
      expiresIn: '1h' 
    },
  );

  return { passwordResetToken, mailAuthToken };
};

export const refreshTokenJwtGenerator = (
  userId: string,
  orgId: string,
  scopedJwtKey: string,
) => {
  const config = getJwtConfig();
  // Read expiry time from environment variable, default to 720h (30 days) if not set
  const expiryTime = (process.env.REFRESH_TOKEN_EXPIRY || '720h') as string;
  
  return jwt.sign(
    { userId: userId, orgId: orgId, scopes: [TokenScopes.TOKEN_REFRESH] },
    scopedJwtKey,
    { 
      algorithm: config.algorithm,
      expiresIn: expiryTime 
    } as jwt.SignOptions,
  );
};

export const iamJwtGenerator = (email: string, scopedJwtKey: string) => {
  const config = getJwtConfig();
  return jwt.sign(
    { email: email, scopes: [TokenScopes.USER_LOOKUP] },
    scopedJwtKey,
    { 
      algorithm: config.algorithm,
      expiresIn: '1h' 
    },
  );
};

export const slackJwtGenerator = (email: string, scopedJwtKey: string) => {
  const config = getJwtConfig();
  return jwt.sign(
    { email: email, scopes: [TokenScopes.CONVERSATION_CREATE] },
    scopedJwtKey,
    { 
      algorithm: config.algorithm,
      expiresIn: '1h' 
    },
  );
};

export const iamUserLookupJwtGenerator = (
  userId: string,
  orgId: string,
  scopedJwtKey: string,
) => {
  const config = getJwtConfig();
  return jwt.sign(
    { userId, orgId, scopes: [TokenScopes.USER_LOOKUP] },
    scopedJwtKey,
    { 
      algorithm: config.algorithm,
      expiresIn: '1h' 
    },
  );
};

export const authJwtGenerator = (
  jwtKey: string,
  email?: string | null,
  userId?: string | null,
  orgId?: string | null,
  fullName?: string | null,
  accountType?: string | null,
) => {
  const config = getJwtConfig();
  // Read expiry time from environment variable, default to 24h if not set
  const expiryTime = (process.env.ACCESS_TOKEN_EXPIRY || '24h') as string;
  
  return jwt.sign(
    { userId, orgId, email, fullName, accountType },
    jwtKey,
    {
      algorithm: config.algorithm,
      expiresIn: expiryTime,
    } as jwt.SignOptions,
  );
};

export const fetchConfigJwtGenerator = (
  userId: string,
  orgId: string,
  scopedJwtKey: string,
) => {
  const config = getJwtConfig();
  return jwt.sign(
    { userId, orgId, scopes: [TokenScopes.FETCH_CONFIG] },
    scopedJwtKey,
    { 
      algorithm: config.algorithm,
      expiresIn: '1h' 
    },
  );
};

export const scopedStorageServiceJwtGenerator = (
  orgId: string,
  scopedJwtKey: string,
) => {
  const config = getJwtConfig();
  return jwt.sign(
    { orgId, scopes: [TokenScopes.STORAGE_TOKEN] },
    scopedJwtKey,
    {
      algorithm: config.algorithm,
      expiresIn: '1h',
    },
  );
};