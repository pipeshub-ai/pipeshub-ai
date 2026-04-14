'use client';

import React, { useRef, useState } from 'react';
import { Box, Flex, Text, Button } from '@radix-ui/themes';
import { isValidEmail } from '@/lib/utils/validators';
import { EmailField, OtpField, OTP_LENGTH } from './form-components';
import type { AuthError } from '../hooks/use-auth-actions';

export interface OtpSignInFlowProps {
  email: string;
  onEmailChange: (email: string) => void;
  /** When true, email cannot be edited (e.g. multiple providers after email step). */
  lockEmail?: boolean;
  /** Reserved for parent flows; no back control in this component. */
  onBack?: () => void;
  sendLoginOtp: () => Promise<boolean>;
  signInWithOtp: (otp: string) => void | Promise<void>;
  otpSendLoading: boolean;
  otpVerifyLoading: boolean;
  error: AuthError | null;
  clearError: () => void;
}

/**
 * Single-screen email OTP sign-in: email + Send OTP, OTP field, Sign In.
 */
export default function OtpSignInFlow({
  email,
  onEmailChange,
  lockEmail = false,
  onBack: _onBack,
  sendLoginOtp,
  signInWithOtp,
  otpSendLoading,
  otpVerifyLoading,
  error,
  clearError,
}: OtpSignInFlowProps) {
  const [otp, setOtp] = useState('');
  const [emailError, setEmailError] = useState('');
  const [otpValidationError, setOtpValidationError] = useState('');
  const emailRef = useRef<HTMLInputElement>(null);
  const otpRef = useRef<HTMLInputElement>(null);

  const trimmedEmail = email.trim();

  const ensureValidEmail = () => {
    if (!trimmedEmail) {
      setEmailError('Email is required.');
      emailRef.current?.focus();
      return false;
    }
    if (!isValidEmail(trimmedEmail)) {
      setEmailError('Please enter a valid email address.');
      emailRef.current?.focus();
      return false;
    }
    setEmailError('');
    return true;
  };

  const handleSendOtp = async () => {
    if (!ensureValidEmail() || otpSendLoading) return;
    clearError();
    await sendLoginOtp();
  };

  const handleSubmitOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (otpVerifyLoading) return;
    setOtpValidationError('');
    if (!ensureValidEmail()) return;
    if (otp.length !== OTP_LENGTH) {
      setOtpValidationError('Please enter the full 6-digit code.');
      otpRef.current?.focus();
      return;
    }
    clearError();
    await signInWithOtp(otp);
  };

  const otpInlineError =
    error?.type === 'generic' && error.message ? error.message : undefined;
  const otpFieldError = otpInlineError ?? otpValidationError;

  const emailLooksValid = !!trimmedEmail && isValidEmail(trimmedEmail);
  /** Send code only needs a valid email. */
  const canSendOtp = emailLooksValid && !otpSendLoading;

  return (
    <Box style={{ width: '100%', maxWidth: '440px' }}>
      <form onSubmit={handleSubmitOtp}>
        <Flex direction="column" gap="4">
          <EmailField
            ref={emailRef}
            value={email}
            onChange={(v) => {
              onEmailChange(v);
              setEmailError('');
              clearError();
            }}
            error={emailError}
            readOnly={lockEmail}
            autoFocus
          />

          <OtpField
            ref={otpRef}
            value={otp}
            onChange={(v) => {
              setOtp(v);
              setOtpValidationError('');
              clearError();
            }}
            error={otpFieldError}
          />

          <Flex justify="end">
            <Text asChild size="2" weight="medium">
              <button
                type="button"
                disabled={!canSendOtp}
                onClick={() => void handleSendOtp()}
                style={{
                  background: 'none',
                  border: 'none',
                  padding: 0,
                  cursor: otpSendLoading ? 'wait' : canSendOtp ? 'pointer' : 'not-allowed',
                  color: canSendOtp ? 'var(--accent-11)' : 'var(--gray-8)',
                  textDecoration: canSendOtp ? 'underline' : 'none',
                  font: 'inherit',
                  fontWeight: 500,
                }}
              >
                {otpSendLoading ? 'Sending…' : 'Send OTP'}
              </button>
            </Text>
          </Flex>

          <Button
            type="submit"
            size="3"
            disabled={otpVerifyLoading}
            style={{
              width: '100%',
              backgroundColor: 'var(--accent-9)',
              color: 'white',
              fontWeight: 500,
              cursor: otpVerifyLoading ? 'not-allowed' : 'pointer',
            }}
          >
            {otpVerifyLoading ? 'Signing in…' : 'Sign In'}
          </Button>
        </Flex>
      </form>
    </Box>
  );
}
