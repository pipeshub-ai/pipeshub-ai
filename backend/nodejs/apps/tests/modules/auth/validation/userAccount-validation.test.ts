import 'reflect-metadata';
import { expect } from 'chai';
import {
  AuthenticateMultiStepResponseSchema,
  AuthenticateResponseSchema,
  DataStringResponseSchema,
  HasPasswordMethodResponseSchema,
  InitAuthResponseSchema,
  LoginOtpGenerateResponseSchema,
  OAuthExchangeResponseSchema,
  RefreshTokenResponseSchema,
  ResetPasswordResponseSchema,
  ValidateEmailChangeResponseSchema,
  initAuthValidationSchema,
  oauthExchangeValidationSchema,
} from '../../../../src/modules/auth/validation/userAccount-validation';

describe('InitAuthResponseSchema (Zod)', () => {
  const minimalValid = {
    currentStep: 0,
    allowedMethods: ['password'],
    message: 'Authentication initialized',
    authProviders: {},
    jitEnabled: false,
  };

  it('should accept the minimal initAuth success shape', () => {
    const result = InitAuthResponseSchema.safeParse(minimalValid);
    expect(result.success).to.be.true;
  });

  it('should accept multiple allowedMethods and nested authProviders', () => {
    const result = InitAuthResponseSchema.safeParse({
      currentStep: 0,
      allowedMethods: ['password', 'google', 'otp'],
      message: 'Authentication initialized',
      authProviders: {
        google: { clientId: 'cid', enableJit: true },
        oauth: {
          providerName: 'custom',
          clientId: 'oid',
          tokenEndpoint: 'https://idp/token',
          authorizationUrl: 'https://idp/authorize',
        },
      },
      jitEnabled: true,
    });
    expect(result.success).to.be.true;
  });

  it('should reject negative currentStep', () => {
    const result = InitAuthResponseSchema.safeParse({
      ...minimalValid,
      currentStep: -1,
    });
    expect(result.success).to.be.false;
  });

  it('should reject non-integer currentStep', () => {
    const result = InitAuthResponseSchema.safeParse({
      ...minimalValid,
      currentStep: 1.5,
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing allowedMethods', () => {
    const { allowedMethods: _, ...rest } = minimalValid;
    const result = InitAuthResponseSchema.safeParse(rest);
    expect(result.success).to.be.false;
  });

  it('should reject jitEnabled when not boolean', () => {
    const result = InitAuthResponseSchema.safeParse({
      ...minimalValid,
      jitEnabled: 'false' as unknown as boolean,
    });
    expect(result.success).to.be.false;
  });

  it('should reject authProviders when not a record', () => {
    const result = InitAuthResponseSchema.safeParse({
      ...minimalValid,
      authProviders: [] as unknown as Record<string, unknown>,
    });
    expect(result.success).to.be.false;
  });
});

describe('AuthenticateResponseSchema (Zod)', () => {
  const minimalValid = {
    message: 'Fully authenticated',
    accessToken: 'header.payload.sig',
    refreshToken: 'header.payload.sig',
  };

  it('should accept the authenticate success shape', () => {
    const result = AuthenticateResponseSchema.safeParse(minimalValid);
    expect(result.success).to.be.true;
  });

  it('should reject empty accessToken', () => {
    const result = AuthenticateResponseSchema.safeParse({
      ...minimalValid,
      accessToken: '',
    });
    expect(result.success).to.be.false;
  });

  it('should reject empty refreshToken', () => {
    const result = AuthenticateResponseSchema.safeParse({
      ...minimalValid,
      refreshToken: '',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing message', () => {
    const { message: _, ...rest } = minimalValid;
    const result = AuthenticateResponseSchema.safeParse(rest);
    expect(result.success).to.be.false;
  });
});

describe('AuthenticateMultiStepResponseSchema (Zod)', () => {
  const minimalValid = {
    status: 'success' as const,
    nextStep: 1,
    allowedMethods: ['password'],
    authProviders: {},
  };

  it('should accept the multi-step authenticate success shape', () => {
    const result = AuthenticateMultiStepResponseSchema.safeParse(minimalValid);
    expect(result.success).to.be.true;
  });

  it('should accept nested authProviders', () => {
    const result = AuthenticateMultiStepResponseSchema.safeParse({
      status: 'success',
      nextStep: 0,
      allowedMethods: ['google', 'oauth'],
      authProviders: {
        google: { clientId: 'x' },
        oauth: {
          providerName: 'p',
          clientId: 'y',
          tokenEndpoint: 'https://idp/token',
          authorizationUrl: 'https://idp/authorize',
        },
      },
    });
    expect(result.success).to.be.true;
  });

  it('should reject status other than success', () => {
    const result = AuthenticateMultiStepResponseSchema.safeParse({
      ...minimalValid,
      status: 'pending',
    });
    expect(result.success).to.be.false;
  });

  it('should reject negative nextStep', () => {
    const result = AuthenticateMultiStepResponseSchema.safeParse({
      ...minimalValid,
      nextStep: -1,
    });
    expect(result.success).to.be.false;
  });
});

describe('LoginOtpGenerateResponseSchema (Zod)', () => {
  it('should accept { message }', () => {
    const result = LoginOtpGenerateResponseSchema.safeParse({
      message: 'OTP sent',
    });
    expect(result.success).to.be.true;
  });

  it('should reject missing message', () => {
    const result = LoginOtpGenerateResponseSchema.safeParse({});
    expect(result.success).to.be.false;
  });
});

describe('ResetPasswordResponseSchema (Zod)', () => {
  it('should accept data + accessToken', () => {
    const result = ResetPasswordResponseSchema.safeParse({
      data: 'password reset',
      accessToken: 'tok',
    });
    expect(result.success).to.be.true;
  });

  it('should accept any non-empty data string', () => {
    const result = ResetPasswordResponseSchema.safeParse({
      data: 'other message',
      accessToken: 'tok',
    });
    expect(result.success).to.be.true;
  });

  it('should reject empty data', () => {
    const result = ResetPasswordResponseSchema.safeParse({
      data: '',
      accessToken: 'tok',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing accessToken', () => {
    const result = ResetPasswordResponseSchema.safeParse({
      data: 'password reset',
    });
    expect(result.success).to.be.false;
  });
});

describe('DataStringResponseSchema (Zod)', () => {
  it('should accept non-empty data (forgot-password style)', () => {
    const result = DataStringResponseSchema.safeParse({
      data: 'password reset mail sent',
    });
    expect(result.success).to.be.true;
  });

  it('should accept non-empty data (email-link reset style)', () => {
    const result = DataStringResponseSchema.safeParse({
      data: 'password reset',
    });
    expect(result.success).to.be.true;
  });

  it('should reject empty data', () => {
    const result = DataStringResponseSchema.safeParse({
      data: '',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing data', () => {
    const result = DataStringResponseSchema.safeParse({});
    expect(result.success).to.be.false;
  });
});

describe('RefreshTokenResponseSchema (Zod)', () => {
  const minimalUser = {
    _id: 'u1',
    orgId: 'o1',
    email: 'a@b.com',
    fullName: 'A B',
    hasLoggedIn: true,
    isDeleted: false,
    slug: 'user-1',
    createdAt: '2026-04-03T07:11:26.330Z',
    updatedAt: '2026-04-03T08:59:47.540Z',
    __v: 0,
  };

  it('should accept user + accessToken', () => {
    const result = RefreshTokenResponseSchema.safeParse({
      user: minimalUser,
      accessToken: 'tok',
    });
    expect(result.success).to.be.true;
  });

  it('should accept full refresh-token user shape', () => {
    const result = RefreshTokenResponseSchema.safeParse({
      user: {
        _id: '69cf681e4dc3febd66ee1f32',
        orgId: '69cf681e4dc3febd66ee1f30',
        fullName: 'Jason',
        email: 'example@example.com',
        hasLoggedIn: true,
        isDeleted: false,
        createdAt: '2026-04-03T07:11:26.330Z',
        updatedAt: '2026-04-03T08:59:47.540Z',
        slug: 'user-1',
        __v: 0,
      },
      accessToken: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.x.y',
    });
    expect(result.success).to.be.true;
  });

  it('should reject empty accessToken', () => {
    const result = RefreshTokenResponseSchema.safeParse({
      user: minimalUser,
      accessToken: '',
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing user', () => {
    const result = RefreshTokenResponseSchema.safeParse({
      accessToken: 'tok',
    });
    expect(result.success).to.be.false;
  });
});

describe('HasPasswordMethodResponseSchema (Zod)', () => {
  it('should accept boolean flag', () => {
    expect(
      HasPasswordMethodResponseSchema.safeParse({ isPasswordAuthEnabled: true })
        .success,
    ).to.be.true;
    expect(
      HasPasswordMethodResponseSchema.safeParse({ isPasswordAuthEnabled: false })
        .success,
    ).to.be.true;
  });

  it('should reject missing isPasswordAuthEnabled', () => {
    const result = HasPasswordMethodResponseSchema.safeParse({});
    expect(result.success).to.be.false;
  });
});

describe('OAuthExchangeResponseSchema (Zod)', () => {
  it('should accept full token payload', () => {
    const result = OAuthExchangeResponseSchema.safeParse({
      access_token: 'at',
      id_token: 'id',
      token_type: 'Bearer',
      expires_in: 3600,
    });
    expect(result.success).to.be.true;
  });

  it('should accept without id_token', () => {
    const result = OAuthExchangeResponseSchema.safeParse({
      access_token: 'at',
      token_type: 'Bearer',
      expires_in: 3600,
    });
    expect(result.success).to.be.true;
  });

  it('should reject empty access_token', () => {
    const result = OAuthExchangeResponseSchema.safeParse({
      access_token: '',
      token_type: 'Bearer',
    });
    expect(result.success).to.be.false;
  });
});

describe('ValidateEmailChangeResponseSchema (Zod)', () => {
  it('should accept message', () => {
    const result = ValidateEmailChangeResponseSchema.safeParse({
      message: 'Email updated successfully',
    });
    expect(result.success).to.be.true;
  });

  it('should reject empty message', () => {
    const result = ValidateEmailChangeResponseSchema.safeParse({
      message: '',
    });
    expect(result.success).to.be.false;
  });
});

describe('oauthExchangeValidationSchema (request)', () => {
  const base = {
    query: {},
    params: {},
    headers: {},
  };

  it('should accept code, provider, redirectUri', () => {
    const result = oauthExchangeValidationSchema.safeParse({
      ...base,
      body: {
        code: 'auth-code',
        provider: 'google',
        redirectUri: 'http://localhost:3000/callback',
      },
    });
    expect(result.success).to.be.true;
  });

  it('should reject empty code', () => {
    const result = oauthExchangeValidationSchema.safeParse({
      ...base,
      body: {
        code: '',
        provider: 'google',
        redirectUri: 'http://localhost/cb',
      },
    });
    expect(result.success).to.be.false;
  });

  it('should reject missing redirectUri', () => {
    const result = oauthExchangeValidationSchema.safeParse({
      ...base,
      body: { code: 'x', provider: 'google' },
    });
    expect(result.success).to.be.false;
  });
});

describe('initAuthValidationSchema (request)', () => {
  it('should accept empty body', () => {
    const result = initAuthValidationSchema.safeParse({
      body: {},
      query: {},
      params: {},
      headers: {},
    });
    expect(result.success).to.be.true;
  });

  it('should accept optional email', () => {
    const result = initAuthValidationSchema.safeParse({
      body: { email: 'user@example.com' },
      query: {},
      params: {},
      headers: {},
    });
    expect(result.success).to.be.true;
  });

  it('should reject invalid email when present', () => {
    const result = initAuthValidationSchema.safeParse({
      body: { email: 'not-an-email' },
      query: {},
      params: {},
      headers: {},
    });
    expect(result.success).to.be.false;
  });
});
