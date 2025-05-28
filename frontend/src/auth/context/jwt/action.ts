import axios, { endpoints } from 'src/utils/axios';

import { CONFIG } from 'src/config-global';

import { STORAGE_KEY } from './constant';
import { setSession, setSessionToken } from './utils';

// ----------------------------------------------------------------------

export type SignInParams = {
  email: string;
  password: string;
};

export type SignUpParams = {
  email: string;
  password: string;
  firstName: string;
  lastName: string;
};

export interface AccountSetupParams {
  accountType: 'individual' | 'business';
  adminFullName: string;
  contactEmail: string;
  password: string;
  confirmPassword: string;
  registeredName?: string;
  shortName?: string;
  permanentAddress?: {
    addressLine1?: string;
    city?: string;
    state?: string;
    postCode?: string;
    country?: string;
  };
  dataCollectionConsent?: boolean;
}

type ForgotPasswordParams = {
  email: string;
};

type GetOtpParams = {
  email: string;
};

type ResetPasswordParams = {
  token: any;
  newPassword: string;
};

type SignInOtpParams = {
  email: string;
  otp: string;
};

// Authentication response interfaces
export interface AuthInitResponse {
  currentStep: number;
  allowedMethods: string[];
  message: string;
  authProviders: Record<string, any>;
}

export interface AuthResponse {
  status?: string;
  nextStep?: number;
  allowedMethods?: string[];
  authProviders?: Record<string, any>;
  accessToken?: string;
  refreshToken?: string;
  message?: string;
}

interface orgExistsReponse {
  exists: boolean;
}

/** **************************************
 * Reset Password
 *************************************** */

export const resetPassword = async ({ token, newPassword }: ResetPasswordParams): Promise<void> => {
  try {
    const config = {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    };

    const params = { password: newPassword };

    await axios.post(`${CONFIG.authUrl}/api/v1/userAccount/password/reset/token`, params, config);
  } catch (error) {
    throw new Error('Error in resetting password', error);
  }
};

export const OrgExists = async (): Promise<orgExistsReponse> => {
  try {
    const response = await axios.get(`${CONFIG.authUrl}/api/v1/org/exists`);
    return response.data;
  } catch (error) {
    throw new Error('Error in resetting password', error);
  }
};

/** **************************************
 * Forgot Password
 *************************************** */

export const forgotPassword = async ({ email }: ForgotPasswordParams): Promise<void> => {
  try {
    const params = { email };
    await axios.post(`${CONFIG.authUrl}/api/v1/userAccount/password/forgot`, params);
  } catch (error) {
    throw new Error('error in sending mail to reset password', error);
  }
};

/** **************************************
 * Get OTP
 *************************************** */
export const sendOtp = async ({ email }: GetOtpParams): Promise<void> => {
  try {
    await axios.post(`${CONFIG.authUrl}/api/v1/userAccount/login/otp/generate`, { email });
  } catch (error) {
    throw new Error('error in sending otp', error);
  }
};

/** **************************************
 * Sign in with OTP
 *************************************** */

export const VerifyOtp = async ({ email, otp }: SignInOtpParams): Promise<AuthResponse> => {
  try {
    const requestBody = {
      email,
      method: 'otp',
      credentials: { otp },
    };

    const res = await axios.post(`${CONFIG.authUrl}/api/v1/userAccount/authenticate`, requestBody);

    const response = res.data as AuthResponse;

    // Check if this is the final step with tokens
    if (response.accessToken && response.refreshToken) {
      setSession(response.accessToken, response.refreshToken);
    }

    return response;
  } catch (error) {
    throw new Error('Error during OTP verification:', error);
  }
};

/** **************************************
 * Auth initialization
 *************************************** */

export const authInitConfig = async (email: string): Promise<AuthInitResponse> => {
  try {
    const response = await axios.post<AuthInitResponse>(
      `${CONFIG.authUrl}/api/v1/userAccount/initAuth`,
      { email }
    );
    const sessionToken = response.headers['x-session-token'];

    // Store the session token if it exists
    if (sessionToken) {
      await setSessionToken(sessionToken);
    }

    return response.data;
  } catch (error) {
    console.log(error);
    // throw error;
    throw new Error(`${error.message}`);
  }
};

/** **************************************
 * Sign in with Password
 *************************************** */

export const signInWithPassword = async ({
  email,
  password,
}: SignInParams): Promise<AuthResponse> => {
  try {
    const requestBody = {
      email,
      method: 'password',
      credentials: { password },
    };

    const res = await axios.post(`${CONFIG.authUrl}/api/v1/userAccount/authenticate`, requestBody);

    const response = res.data as AuthResponse;

    // Check if this is the final step with tokens
    if (response.accessToken && response.refreshToken) {
      setSession(response.accessToken, response.refreshToken);
    }

    return response;
  } catch (error) {
    throw new Error('Error during password authentication:', error);
  }
};

/** **************************************
 * Sign in with Google
 *************************************** */

export const SignInWithGoogle = async ({
  credential,
}: {
  credential: string;
}): Promise<AuthResponse> => {
  try {
    const res = await axios.post(`${CONFIG.authUrl}/api/v1/userAccount/authenticate`, {
      credentials: credential,
      method: 'google',
    });

    const response = res.data as AuthResponse;

    // Check if this is the final step with tokens
    if (response.accessToken && response.refreshToken) {
      setSession(response.accessToken, response.refreshToken);
    }

    return response;
  } catch (error) {
    throw new Error('Error during sign in with Google:', error);
  }
};

interface AzureCredientals {
  accessToken: string;
  idToken: string;
}

export const SignInWithAzureAd = async (credential: AzureCredientals): Promise<AuthResponse> => {
  try {
    const res = await axios.post(`${CONFIG.authUrl}/api/v1/userAccount/authenticate`, {
      credentials: credential,
      method: 'azureAd',
    });

    const response = res.data as AuthResponse;

    // Check if this is the final step with tokens
    if (response.accessToken && response.refreshToken) {
      setSession(response.accessToken, response.refreshToken);
    }

    return response;
  } catch (error) {
    throw new Error('Error during sign in with Google:', error);
  }
};

interface MicrosoftCredientals {
  accessToken: string;
  idToken: string;
}

export const SignInWithMicrosoft = async (
  credential: MicrosoftCredientals
): Promise<AuthResponse> => {
  try {
    const res = await axios.post(`${CONFIG.authUrl}/api/v1/userAccount/authenticate`, {
      credentials: credential,
      method: 'microsoft',
    });

    const response = res.data as AuthResponse;

    // Check if this is the final step with tokens
    if (response.accessToken && response.refreshToken) {
      setSession(response.accessToken, response.refreshToken);
    }

    return response;
  } catch (error) {
    throw new Error('Error during sign in with Google:', error);
  }
};

interface OAuthCredentials {
  accessToken?: string;
  idToken?: string;
}

export const SignInWithOAuth = async (credential: OAuthCredentials): Promise<AuthResponse> => {
  try {
    const res = await axios.post(`${CONFIG.authUrl}/api/v1/userAccount/authenticate`, {
      credentials: credential,
      method: 'oauth',
    });

    const response = res.data as AuthResponse;

    // Check if this is the final step with tokens
    if (response.accessToken && response.refreshToken) {
      setSession(response.accessToken, response.refreshToken);
    }

    return response;
  } catch (error) {
    throw new Error('Error during OAuth authentication:', error);
  }
};

/** **************************************
 * Account setup
 *************************************** */

export const AccountSetUp = async (accountSetupData: AccountSetupParams): Promise<void> => {
  try {
    const res = await axios.post(`${CONFIG.authUrl}/api/v1/org`, accountSetupData);

    if (accountSetupData.accountType === 'business') {
      // Extract organization details from response
      const { _id } = res.data;

      if (!_id) {
        throw new Error('Organization ID not found in response');
      }
    } else {
      // Extract user details from response for individual accounts
      const { _id } = res.data;

      if (!_id) {
        throw new Error('User ID not found in response');
      }
    }
  } catch (error) {
    throw new Error('Error during account setup:', error);
  }
};

/** **************************************
 * Sign up
 *************************************** */

export const signUp = async ({
  email,
  password,
  firstName,
  lastName,
}: SignUpParams): Promise<void> => {
  const params = {
    email,
    password,
    firstName,
    lastName,
  };

  try {
    const res = await axios.post(endpoints.auth.signUp, params);

    const { accessToken } = res.data;

    if (!accessToken) {
      throw new Error('Access token not found in response');
    }

    localStorage.setItem(STORAGE_KEY, accessToken);
  } catch (error) {
    throw new Error('Error during sign up:', error);
  }
};

/** **************************************
 * Sign out
 *************************************** */
export const signOut = async (): Promise<void> => {
  try {
    await setSession(null, null);
  } catch (error) {
    throw new Error('Error during sign out:', error);
  }
};
