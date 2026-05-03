'use client';

import { useState, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthStore } from '@/lib/store/auth-store';
import { toast } from '@/lib/store/toast-store';
import { fetchAndSetCurrentUser } from '@/lib/auth/hydrate-user';
import { AuthApi } from '../api';
import {
  getUserAccountApiErrorMessage,
  getUserAccountApiResponseMessage,
  getUserAccountBlockedUntil,
} from '@/lib/api/user-account-api-error';

// ─── Local error classification (login-module only) ───────────────────────────

type AuthErrorKind = 'wrongPassword' | 'accessRevoked' | 'noPasswordSet' | 'generic';

type HttpError = {
  response?: { data?: { error?: { message?: string }; message?: string }; status?: number };
  message?: string;
};

function rawApiErrorMessage(err: unknown): string {
  const e = err as HttpError;
  return (
    e.response?.data?.error?.message ??
    e.response?.data?.message ??
    e.message ??
    ''
  );
}

function extractErrorMessage(error: unknown): string {
  const fromBody = getUserAccountApiResponseMessage(error);
  if (fromBody) return fromBody.toLowerCase();
  const e = error as HttpError;
  return (
    e?.response?.data?.error?.message ??
    e?.response?.data?.message ??
    e?.message ??
    ''
  ).toLowerCase();
}

function formatBlockedUntilLocal(blockedUntilIso?: string): string | undefined {
  if (!blockedUntilIso) {
    return undefined;
  }

  const blockedUntilDate = new Date(blockedUntilIso);
  if (Number.isNaN(blockedUntilDate.getTime())) {
    return undefined;
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(blockedUntilDate);
}

function getBlockedUntilDescription(error: unknown): string | undefined {
  const blockedUntilIso = getUserAccountBlockedUntil(error);
  const blockedUntilLocal = formatBlockedUntilLocal(blockedUntilIso);
  if (!blockedUntilLocal) {
    return undefined;
  }

  return `Try again after ${blockedUntilLocal}.`;
}

function classifyAuthError(error: unknown): AuthErrorKind {
  const status: number = (error as HttpError)?.response?.status ?? 0;
  const msg = extractErrorMessage(error);

  // Account blocked / access revoked / removed
  if (
    status === 403 ||
    msg.includes('blocked') ||
    msg.includes('disabled') ||
    msg.includes('revoked') ||
    msg.includes('suspended') ||
    msg.includes('deleted') ||
    msg.includes('restore your account')
  ) {
    return 'accessRevoked';
  }

  // No password created yet
  if (msg.includes('not created a password') || msg.includes('no password')) {
    return 'noPasswordSet';
  }

  // Wrong password or user-not-found (avoid leaking account existence)
  if (
    msg.includes('incorrect') ||
    msg.includes('invalid password') ||
    msg.includes('wrong password') ||
    status === 404
  ) {
    return 'wrongPassword';
  }

  return 'generic';
}

// ─── Types ────────────────────────────────────────────────────────────────────

export interface AuthError {
  type: 'wrongPassword' | 'accessRevoked' | 'noPasswordSet' | 'generic';
  message?: string;
}

export interface UseAuthActionsOptions {
  /** Current email (used for password sign-in and forgot-password). */
  email: string;
  /** Provider-specific config returned by initAuth. */
  authProviders?: Record<string, Record<string, string>>;
  /** Optional post-auth redirect destination. */
  redirectTo?: string;
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

/**
 * useAuthActions — single source of truth for all sign-in actions.
 *
 * Handles: password sign-in (API → token storage → redirect),
 * forgot-password (API → toast), SSO / Google / Microsoft redirects.
 *
 * Every form screen pulls from this hook instead of reimplementing auth logic.
 */
export function useAuthActions({
  email,
  authProviders: _authProviders = {},
  redirectTo,
}: UseAuthActionsOptions) {
  const router = useRouter();
  const setTokens = useAuthStore((s) => s.setTokens);
  const setUser = useAuthStore((s) => s.setUser);

  const [loading, setLoading] = useState(false);
  const [forgotLoading, setForgotLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const [microsoftLoading, setMicrosoftLoading] = useState(false);
  const [oauthLoading, setOauthLoading] = useState(false);
  const [otpSendLoading, setOtpSendLoading] = useState(false);
  const [otpVerifyLoading, setOtpVerifyLoading] = useState(false);
  const [error, setError] = useState<AuthError | null>(null);
  /** Prevents overlapping Microsoft backend auth if onSuccess fired more than once. */
  const microsoftSignInInFlightRef = useRef(false);

  const postAuthRedirectTo = redirectTo || '/chat';
  const clearError = useCallback(() => setError(null), []);
  const checkUserSession = useCallback(async (): Promise<boolean> => {
    return fetchAndSetCurrentUser();
  }, []);

  // ── Password sign-in ────────────────────────────────────────────────────────

  const signInWithPassword = useCallback(
    async (password: string) => {
      if (loading || !password) return;
      setLoading(true);
      setError(null);

      try {
        const response = await AuthApi.signInWithPassword(email, password);

        if (response.accessToken && response.refreshToken) {
          setTokens(response.accessToken, response.refreshToken);
          if (response.user) {
            setUser(response.user);
          }
          // Store email for returning-user auto-login
          if (typeof window !== 'undefined') {
            localStorage.setItem('pipeshub_last_email', email);
          }
          router.push(postAuthRedirectTo);
        } else {
          setError({
            type: 'generic',
            message: response.message ?? 'Unexpected response. Please try again.',
          });
        }
      } catch (err: unknown) {
        const kind = classifyAuthError(err);
        const bodyMsg = getUserAccountApiResponseMessage(err);
        const apiMsg = getUserAccountApiErrorMessage(
          err,
          'Sign in failed. Please try again.',
        );

        if (kind === 'accessRevoked') {
          const blockedUntilDescription = getBlockedUntilDescription(err);
          if (bodyMsg) {
            toast.error(bodyMsg, {
              description: blockedUntilDescription,
              duration: null,
              showCloseButton: true,
            });
          } else {
            toast.error('Your account has been disabled.', {
              description:
                blockedUntilDescription ??
                'You have entered incorrect credentials too many times',
              duration: null,
              showCloseButton: true,
            });
          }
        } else if (kind === 'noPasswordSet') {
          toast.info('No password set for this account.', {
            description: 'Use Forgot Password below to set up your password.',
          });
        } else {
          toast.error(bodyMsg ?? apiMsg, { showCloseButton: true });
        }

        setError({
          type: kind,
          message:
            kind === 'generic' || kind === 'wrongPassword' ? apiMsg : undefined,
        });
      } finally {
        setLoading(false);
      }
    },
    [email, loading, postAuthRedirectTo, router, setTokens, setUser],
  );

  // ── Forgot password ─────────────────────────────────────────────────────────

  const forgotPassword = useCallback(async () => {
    if (forgotLoading) return;
    setForgotLoading(true);
    try {
      await AuthApi.forgotPassword(email);
      toast.success('Check your inbox', {
        description:
          'If an account exists for this email, a password reset link has been sent.',
      });
    } catch (err: unknown) {
      const rawMsg = rawApiErrorMessage(err);
      toast.error('Could not request password reset', {
        description: rawMsg || 'Please try again later.',
      });
    } finally {
      setForgotLoading(false);
    }
  }, [email, forgotLoading]);

  // ── OAuth / SSO redirects ───────────────────────────────────────────────────

  /**
   * SAML SSO redirect.
   * Uses the /api/v1/saml/signIn endpoint and appends the current auth session
   * token (if present) as a `sessionToken` query parameter so the backend can
   * correlate the SAML assertion with this auth session.
   */
  const redirectToSSO = useCallback(() => {
    const sessionToken =
      typeof window !== 'undefined'
        ? sessionStorage.getItem('auth_session_token')
        : null;
    // Default to '' so a missing build-time env var resolves to a same-origin
    // URL instead of the literal string "undefined" in the path.
    const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? '';
    let url = `${baseUrl}/api/v1/saml/signIn?email=${encodeURIComponent(email)}`;
    if (sessionToken) {
      url += `&sessionToken=${encodeURIComponent(sessionToken)}`;
    }
    window.location.href = url;
  }, [email]);

  /**
   * Google sign-in via custom popup flow.
   * Receives the id_token credential from GoogleSignInButton and
   * authenticates it against the backend.
   */
  const signInWithGoogle = useCallback(
    async (credential: string) => {
      if (googleLoading) return;
      setGoogleLoading(true);
      setError(null);
      try {
        const response = await AuthApi.signInWithGoogle(credential);
        if (response.accessToken && response.refreshToken) {
          setTokens(response.accessToken, response.refreshToken);
          if (response.user) setUser(response.user);
          if (typeof window !== 'undefined') {
            localStorage.setItem('pipeshub_last_email', email);
          }
          router.push(postAuthRedirectTo);
        } else {
          setError({
            type: 'generic',
            message: response.message ?? 'Google sign-in failed. Please try again.',
          });
        }
      } catch (err: unknown) {
        const kind = classifyAuthError(err);
        const bodyMsg = getUserAccountApiResponseMessage(err);
        const apiMsg = getUserAccountApiErrorMessage(
          err,
          'Google sign-in failed. Please try again.',
        );
        if (kind === 'accessRevoked') {
          const blockedUntilDescription = getBlockedUntilDescription(err);
          if (bodyMsg) {
            toast.error(bodyMsg, {
              description: blockedUntilDescription,
              duration: null,
              showCloseButton: true,
            });
          } else {
            toast.error('Your account has been disabled.', {
              description: blockedUntilDescription ?? 'Please contact your administrator.',
              duration: null,
              showCloseButton: true,
            });
          }
        } else {
          toast.error(bodyMsg ?? apiMsg, { showCloseButton: true });
        }
        setError({ type: kind, message: kind === 'generic' ? apiMsg : undefined });
      } finally {
        setGoogleLoading(false);
      }
    },
    [email, googleLoading, postAuthRedirectTo, router, setTokens, setUser],
  );

  /**
   * Generic OAuth sign-in via popup flow.
   * Receives the provider access token from OAuthSignInButton (after the
   * callback page exchanged the authorization code) and authenticates it
   * against the backend with method: 'oauth'.
   */
  const signInWithOAuth = useCallback(
    async (accessToken: string) => {
      if (oauthLoading) return;
      setOauthLoading(true);
      setError(null);
      try {
        const response = await AuthApi.signInWithOAuth(accessToken);
        if (response.accessToken && response.refreshToken) {
          setTokens(response.accessToken, response.refreshToken);
          if (response.user) setUser(response.user);
          if (typeof window !== 'undefined') {
            localStorage.setItem('pipeshub_last_email', email);
          }
          router.push(postAuthRedirectTo);
        } else if (
          response.nextStep !== undefined &&
          response.allowedMethods &&
          response.allowedMethods.length > 0
        ) {
          toast.info('Additional verification required', {
            description: response.message ?? 'Complete the next step to continue.',
          });
          setError({
            type: 'generic',
            message: response.message ?? 'Additional verification required.',
          });
        } else {
          setError({
            type: 'generic',
            message: response.message ?? 'OAuth sign-in failed. Please try again.',
          });
        }
      } catch (err: unknown) {
        const kind = classifyAuthError(err);
        const bodyMsg = getUserAccountApiResponseMessage(err);
        const apiMsg = getUserAccountApiErrorMessage(
          err,
          'OAuth sign-in failed. Please try again.',
        );
        if (kind === 'accessRevoked') {
          const blockedUntilDescription = getBlockedUntilDescription(err);
          if (bodyMsg) {
            toast.error(bodyMsg, {
              description: blockedUntilDescription,
              duration: null,
              showCloseButton: true,
            });
          } else {
            toast.error('Your account has been disabled.', {
              description: blockedUntilDescription ?? 'Please contact your administrator.',
              duration: null,
              showCloseButton: true,
            });
          }
        } else {
          toast.error(bodyMsg ?? apiMsg, { showCloseButton: true });
        }
        setError({ type: kind, message: kind === 'generic' ? apiMsg : undefined });
      } finally {
        setOauthLoading(false);
      }
    },
    [email, oauthLoading, postAuthRedirectTo, router, setTokens, setUser],
  );

  const sendLoginOtp = useCallback(async (): Promise<boolean> => {
    if (otpSendLoading) return false;
    setOtpSendLoading(true);
    setError(null);
    try {
      await AuthApi.generateLoginOtp(email.trim());
      toast.success('Check your email', {
        description: 'We sent a verification code to your inbox.',
      });
      return true;
    } catch (err: unknown) {
      const rawMsg = rawApiErrorMessage(err);
      toast.error('Could not send verification code', {
        description: rawMsg || 'Please try again later.',
      });
      // setError({
      //   type: 'generic',
      //   message: rawMsg || 'Could not send the code. Please try again.',
      // });
      return false;
    } finally {
      setOtpSendLoading(false);
    }
  }, [email, otpSendLoading]);

  const signInWithOtp = useCallback(
    async (otp: string) => {
      if (otpVerifyLoading || !otp) return;
      setOtpVerifyLoading(true);
      setError(null);
      try {
        const response = await AuthApi.signInWithOtp(email.trim(), otp);

        if (response.accessToken && response.refreshToken) {
          setTokens(response.accessToken, response.refreshToken);
          if (response.user) {
            setUser(response.user);
          }
          if (typeof window !== 'undefined') {
            localStorage.setItem('pipeshub_last_email', email.trim());
          }
          router.push(postAuthRedirectTo);
          return;
        }

        if (
          response.nextStep !== undefined &&
          response.allowedMethods &&
          response.allowedMethods.length > 0
        ) {
          toast.info('Additional verification required', {
            description: response.message ?? 'Complete the next step to continue.',
          });
          setError({
            type: 'generic',
            message: response.message ?? 'Additional verification required.',
          });
          return;
        }

        const unexpectedMsg =
          response.message ?? 'Unexpected response. Please try again.';
        toast.error('Sign-in failed', {
          description: unexpectedMsg,
        });
        setError({
          type: 'generic',
          message: unexpectedMsg,
        });
      } catch (err: unknown) {
        const kind = classifyAuthError(err);
        const bodyMsg = getUserAccountApiResponseMessage(err);
        const apiMsg = getUserAccountApiErrorMessage(
          err,
          'Verification failed. Please try again.',
        );

        if (kind === 'accessRevoked') {
          const blockedUntilDescription = getBlockedUntilDescription(err);
          if (bodyMsg) {
            toast.error(bodyMsg, {
              description: blockedUntilDescription,
              duration: null,
              showCloseButton: true,
            });
          } else {
            toast.error('Your account has been disabled.', {
              description:
                blockedUntilDescription ??
                'You have entered incorrect credentials too many times',
              duration: null,
              showCloseButton: true,
            });
          }
        } else {
          toast.error(bodyMsg ?? apiMsg, { showCloseButton: true });
        }

        setError({
          type: 'generic',
          message: apiMsg,
        });
      } finally {
        setOtpVerifyLoading(false);
      }
    },
    [email, otpVerifyLoading, postAuthRedirectTo, router, setTokens, setUser],
  );

  const signInWithMicrosoft = useCallback(
    async (
      credentials: { accessToken: string; idToken: string },
      method: 'microsoft' | 'azureAd' = 'microsoft',
    ) => {
      if (microsoftLoading || microsoftSignInInFlightRef.current) return;
      microsoftSignInInFlightRef.current = true;
      setMicrosoftLoading(true);
      setError(null);
      try {
        const response = await AuthApi.signInWithMicrosoft(credentials, method);
        if (response.accessToken && response.refreshToken) {
          setTokens(response.accessToken, response.refreshToken);
          if (response.user) setUser(response.user);
          if (typeof window !== 'undefined') {
            localStorage.setItem('pipeshub_last_email', email);
          }
          router.push(postAuthRedirectTo);
        } else {
          setError({
            type: 'generic',
            message: response.message ?? 'Microsoft sign-in failed. Please try again.',
          });
        }
      } catch (err: unknown) {
        const kind = classifyAuthError(err);
        const bodyMsg = getUserAccountApiResponseMessage(err);
        const apiMsg = getUserAccountApiErrorMessage(
          err,
          'Microsoft sign-in failed. Please try again.',
        );
        if (kind === 'accessRevoked') {
          const blockedUntilDescription = getBlockedUntilDescription(err);
          if (bodyMsg) {
            toast.error(bodyMsg, {
              description: blockedUntilDescription,
              duration: null,
              showCloseButton: true,
            });
          } else {
            toast.error('Your account has been disabled.', {
              description: blockedUntilDescription ?? 'Please contact your administrator.',
              duration: null,
              showCloseButton: true,
            });
          }
        } else {
          toast.error(bodyMsg ?? apiMsg, { showCloseButton: true });
        }
        setError({ type: kind, message: kind === 'generic' ? apiMsg : undefined });
      } finally {
        microsoftSignInInFlightRef.current = false;
        setMicrosoftLoading(false);
      }
    },
    [email, microsoftLoading, postAuthRedirectTo, router, setTokens, setUser],
  );

  return {
    signInWithPassword,
    forgotPassword,
    redirectToSSO,
    signInWithGoogle,
    signInWithMicrosoft,
    signInWithOAuth,
    sendLoginOtp,
    signInWithOtp,
    checkUserSession,
    clearError,
    loading,
    forgotLoading,
    googleLoading,
    microsoftLoading,
    oauthLoading,
    otpSendLoading,
    otpVerifyLoading,
    error,
  };
}
