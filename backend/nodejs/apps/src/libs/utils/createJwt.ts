import jwt from 'jsonwebtoken';
import { TokenScopes } from '../enums/token-scopes.enum';

export const mailJwtGenerator = (email: string, scopedJwtSecret: string) => {
  return jwt.sign(
    { email: email, scopes: [TokenScopes.SEND_MAIL] },
    scopedJwtSecret,
    {
      expiresIn: '1h',
    },
  );
};

export const jwtGeneratorForForgotPasswordLink = (
  userEmail: string,
  userId: string,
  orgId: string,
  scopedJwtSecret: string,
) => {
  // Token for password reset
  const passwordResetToken = jwt.sign(
    {
      userEmail,
      userId,
      orgId,
      scopes: [TokenScopes.PASSWORD_RESET],
    },
    scopedJwtSecret,
    { expiresIn: '20m' },
  );
  const mailAuthToken = jwt.sign(
    {
      userEmail,
      userId,
      orgId,
      scopes: [TokenScopes.SEND_MAIL],
    },
    scopedJwtSecret,
    { expiresIn: '1h' },
  );

  return { passwordResetToken, mailAuthToken };
};

export const jwtGeneratorForNewAccountPassword = (
  userEmail: string,
  userId: string,
  orgId: string,
  scopedJwtSecret: string,
) => {
  // Token for password reset
  const passwordResetToken = jwt.sign(
    {
      userEmail,
      userId,
      orgId,
      scopes: [TokenScopes.PASSWORD_RESET],
    },
    scopedJwtSecret,
    { expiresIn: '48h' },
  );
  const mailAuthToken = jwt.sign(
    {
      userEmail,
      userId,
      orgId,
      scopes: [TokenScopes.SEND_MAIL],
    },
    scopedJwtSecret,
    { expiresIn: '1h' },
  );

  return { passwordResetToken, mailAuthToken };
};

export const refreshTokenJwtGenerator = (
  userId: string,
  orgId: string,
  scopedJwtSecret: string,
) => {
  // Read expiry time from environment variable, default to 720h (30 days) if not set
  const expiryTime = (process.env.REFRESH_TOKEN_EXPIRY || '720h') as string;
  
  return jwt.sign(
    { userId: userId, orgId: orgId, scopes: [TokenScopes.TOKEN_REFRESH] },
    scopedJwtSecret,
    { expiresIn: expiryTime } as jwt.SignOptions,
  );
};

export const iamJwtGenerator = (email: string, scopedJwtSecret: string) => {
  return jwt.sign(
    { email: email, scopes: [TokenScopes.USER_LOOKUP] },
    scopedJwtSecret,
    { expiresIn: '1h' },
  );
};

export const slackJwtGenerator = (email: string, scopedJwtSecret: string,scopes?: TokenScopes[]) => {
  return jwt.sign(
    { email: email, scopes: scopes || [TokenScopes.CONVERSATION_CREATE] },
    scopedJwtSecret,
    { expiresIn: '1h' },
  );
};


export const iamUserLookupJwtGenerator = (
  userId: string,
  orgId: string,
  scopedJwtSecret: string,
) => {
  return jwt.sign(
    { userId, orgId, scopes: [TokenScopes.USER_LOOKUP] },
    scopedJwtSecret,
    { expiresIn: '1h' },
  );
};

export const authJwtGenerator = (
  scopedJwtSecret: string,
  email?: string | null,
  userId?: string | null,
  orgId?: string | null,
  fullName?: string | null,
  accountType?: string | null,
) => {
  // Read expiry time from environment variable, default to 24h if not set
  const expiryTime = (process.env.ACCESS_TOKEN_EXPIRY || '24h') as string;
  
  return jwt.sign(
    { userId, orgId, email, fullName, accountType },
    scopedJwtSecret,
    {
      expiresIn: expiryTime,
    } as jwt.SignOptions,
  );
};

export const fetchConfigJwtGenerator = (
  userId: string,
  orgId: string,
  scopedJwtSecret: string,
) => {
  return jwt.sign(
    { userId, orgId, scopes: [TokenScopes.FETCH_CONFIG] },
    scopedJwtSecret,
    { expiresIn: '1h' },
  );
};

export const scopedStorageServiceJwtGenerator = (
  orgId: string,
  scopedJwtSecret: string,
) => {
  return jwt.sign(
    { orgId, scopes: [TokenScopes.STORAGE_TOKEN] },
    scopedJwtSecret,
    {
      expiresIn: '1h',
    },
  );
};
