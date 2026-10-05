import jwt from 'jsonwebtoken';
import { TokenScopes } from '../enums/token-scopes.enum';
import { deriveUserActionSecret } from './jwtKeys';

const signUserActionToken = (
  payload: object,
  scopedJwtSecret: string,
  options: jwt.SignOptions,
): string =>
  jwt.sign(payload, deriveUserActionSecret(scopedJwtSecret), options);

export const mailJwtGenerator = (email: string, scopedJwtSecret: string) => {
  return jwt.sign(
    { email: email, scopes: [TokenScopes.SEND_MAIL] },
    scopedJwtSecret,
    {
      expiresIn: '1h',
    },
  );
};

const DEFAULT_PASSWORD_RESET_LINK_EXPIRY = '20m';

// The units jsonwebtoken's duration parser (`ms`) accepts, less milliseconds,
// which no link should be measured in: name, pattern, seconds per unit.
const LIFETIME_UNITS: ReadonlyArray<[string, RegExp, number]> = [
  ['second', /^(s|secs?|seconds?)$/i, 1],
  ['minute', /^(m|mins?|minutes?)$/i, 60],
  ['hour', /^(h|hrs?|hours?)$/i, 60 * 60],
  ['day', /^(d|days?)$/i, 24 * 60 * 60],
  ['week', /^(w|weeks?)$/i, 7 * 24 * 60 * 60],
  ['year', /^(y|yrs?|years?)$/i, 365.25 * 24 * 60 * 60],
];

export interface LinkLifetime {
  /** Whole seconds, the form jsonwebtoken reads a number `expiresIn` as. */
  seconds: number;
  /** How the email puts it: "20 minutes", "90 seconds". */
  description: string;
}

/**
 * Reads a lifetime such as `20m`, `1.5h`, `2 days` or `90`. A bare number is
 * seconds. It is converted here, because jsonwebtoken reads a unitless string
 * as milliseconds, so `'90'` passed straight through would expire at once.
 * Anything else, or a lifetime under one second, is refused.
 */
export const parseLinkLifetime = (
  value: string | number,
  settingName?: string,
): LinkLifetime => {
  const text = String(value).trim();
  const match = /^(\d+(?:\.\d+)?)\s*([a-z]*)$/i.exec(text);
  const unitText = match?.[2] ?? '';
  const unit =
    unitText === ''
      ? LIFETIME_UNITS[0]
      : LIFETIME_UNITS.find(([, pattern]) => pattern.test(unitText));
  const amountText = match?.[1] ?? '';
  // Checked before rounding, so 0.5s is refused rather than rounded up to 1s.
  const exactSeconds = Number(amountText) * (unit?.[2] ?? 0);
  const seconds = Math.round(exactSeconds);
  if (!match || !unit || !(exactSeconds >= 1) || !Number.isFinite(seconds)) {
    throw new Error(
      `${settingName === undefined ? '' : `${settingName}: `}"${text}" is not ` +
        'a usable link lifetime. Use a positive duration such as 20m, 1h or ' +
        '2d, or a whole number of seconds such as 90.',
    );
  }
  const amount = Number(amountText);
  return {
    seconds,
    description: `${amountText} ${unit[0]}${amount === 1 ? '' : 's'}`,
  };
};

/** `20m` → "20 minutes"; a number is seconds. */
export const describeLinkLifetime = (value: string | number): string =>
  parseLinkLifetime(value).description;

/**
 * The forgot-password link lifetime from PASSWORD_RESET_LINK_EXPIRY, default
 * 20 minutes. Throws on an unusable value, naming the variable, so a typo
 * fails startup instead of issuing links that are dead on arrival.
 */
export const passwordResetLinkLifetime = (): LinkLifetime => {
  const configured = process.env.PASSWORD_RESET_LINK_EXPIRY?.trim() ?? '';
  return parseLinkLifetime(
    configured === '' ? DEFAULT_PASSWORD_RESET_LINK_EXPIRY : configured,
    'PASSWORD_RESET_LINK_EXPIRY',
  );
};

export const jwtGeneratorForForgotPasswordLink = (
  userEmail: string,
  userId: string,
  orgId: string,
  scopedJwtSecret: string,
) => {
  // Token for password reset
  const passwordResetToken = signUserActionToken(
    {
      userEmail,
      userId,
      orgId,
      scopes: [TokenScopes.PASSWORD_RESET],
    },
    scopedJwtSecret,
    { expiresIn: passwordResetLinkLifetime().seconds },
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
  const passwordResetToken = signUserActionToken(
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

export const newAccountPasswordLink = (
  frontendUrl: string,
  userEmail: string,
  userId: string,
  orgId: string,
  scopedJwtSecret: string,
): string => {
  const { passwordResetToken } = jwtGeneratorForNewAccountPassword(
    userEmail,
    userId,
    orgId,
    scopedJwtSecret,
  );
  return `${frontendUrl}/reset-password#token=${passwordResetToken}`;
};

export const refreshTokenJwtGenerator = (
  userId: string,
  orgId: string,
  scopedJwtSecret: string,
) => {
  // Read expiry time from environment variable, default to 720h (30 days) if not set
  const expiryTime = (process.env.REFRESH_TOKEN_EXPIRY || '720h') as string;

  return signUserActionToken(
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
  role?: 'admin' | 'member' | null,
) => {
  // Read expiry time from environment variable, default to 24h if not set
  const expiryTime = (process.env.ACCESS_TOKEN_EXPIRY || '24h') as string;

  const payload: Record<string, unknown> = {
    userId,
    orgId,
    email,
    fullName,
    accountType,
  };
  if (role === 'admin' || role === 'member') {
    payload.role = role;
  }

  return jwt.sign(payload, scopedJwtSecret, {
    expiresIn: expiryTime,
  } as jwt.SignOptions);
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
  userId?: string,
) => {
  return jwt.sign(
    // Carry userId so the storage service can still attribute the document to
    // its initiator (extractUserId reads it) when the request is made with this
    // service token instead of the user's JWT.
    { orgId, ...(userId ? { userId } : {}), scopes: [TokenScopes.STORAGE_TOKEN] },
    scopedJwtSecret,
    {
      expiresIn: '1h',
    },
  );
};

export const jwtGeneratorForValidateEmailLink = (
  userEmail: string,
  newEmail: string,
  userId: string,
  orgId: string,
  scopedJwtSecret: string,
) => {
  const validateEmailToken = signUserActionToken(
    {
      userEmail,
      userId,
      orgId,
      newEmail,
      scopes: [TokenScopes.VALIDATE_EMAIL],
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

  return { validateEmailToken, mailAuthToken };
};

export const jwtGeneratorForOrgEmailVerification = (
  orgId: string,
  contactEmail: string,
  scopedJwtSecret: string,
  smtpOrgId: string,
) => {
  const orgVerificationToken = signUserActionToken(
    {
      orgId,
      contactEmail,
      scopes: [TokenScopes.ORG_EMAIL_VERIFY],
    },
    scopedJwtSecret,
    { expiresIn: '24h' },
  );
  // Use smtpOrgId (admin org) so the communication service resolves the
  // admin org's SMTP config — the new org has none configured yet.
  const mailAuthToken = jwt.sign(
    { contactEmail, orgId: smtpOrgId, scopes: [TokenScopes.SEND_MAIL] },
    scopedJwtSecret,
    { expiresIn: '25h' },
  );
  return { orgVerificationToken, mailAuthToken };
};

export const jwtGeneratorForOtpMail = (
  email: string,
  adminOrgId: string,
  scopedJwtSecret: string,
) => {
  return jwt.sign(
    { email, orgId: adminOrgId, scopes: [TokenScopes.SEND_MAIL] },
    scopedJwtSecret,
    { expiresIn: '10m' },
  );
};

export const jwtGeneratorForEmailVerified = (
  email: string,
  scopedJwtSecret: string,
  hashProof: string[] = [],
) => {
  const expiryTime = (process.env.EMAIL_VERIFIED_TOKEN_EXPIRY || '30d') as string;
  return signUserActionToken(
    { email, scopes: [TokenScopes.EMAIL_VERIFIED], hashProof },
    scopedJwtSecret,
    { expiresIn: expiryTime } as jwt.SignOptions,
  );
};

export const jwtGeneratorForMailAuth = (
  userEmail: string,
  userId: string,
  orgId: string,
  scopedJwtSecret: string,
): string => {
  return jwt.sign(
    {
      userEmail,
      userId,
      orgId,
      scopes: [TokenScopes.SEND_MAIL],
    },
    scopedJwtSecret,
    { expiresIn: '1h' },
  );
};



