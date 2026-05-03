'use client';

import React, { useState } from 'react';
import { Flex } from '@radix-ui/themes';
import { isValidEmail } from '@/lib/utils/validators';
import AuthTitleSection from '../components/auth-title-section';
import { EmailField } from './form-components';
import { AuthApi, type AuthInitResponse } from '../api';
import { LoadingButton } from '@/app/components/ui/loading-button';

// ─── Props ────────────────────────────────────────────────────────────────────

export interface EmailStepProps {
  /** Called after a successful initAuth. Passes the email + server response. */
  onNext: (email: string, response: AuthInitResponse) => void;
  /** Optional pre-filled email (e.g. coming back via back-navigation). */
  initialEmail?: string;
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * EmailStep — step 1 of the login flow.
 *
 * Collects the user's email, calls initAuth, and hands off
 * the allowed-methods response to the page orchestrator.
 */
export default function EmailStep({ onNext, initialEmail = '' }: EmailStepProps) {
  const [email, setEmail] = useState(initialEmail);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleContinue = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || loading) return;

    if (!isValidEmail(trimmed)) {
      setError('Please enter a valid email address.');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const response = await AuthApi.initAuth();
      // Persist for returning-user flow – login page will read this and
      // auto-call initAuth to skip the email step on next visit.
      if (typeof window !== 'undefined') {
        localStorage.setItem('pipeshub_last_email', trimmed);
      }
      onNext(trimmed, response);
    } catch (err: unknown) {
      type HttpErr = { response?: { data?: { message?: string }; status?: number }; message?: string };
      const e = err as HttpErr;
      const msg =
        e?.response?.data?.message ||
        e?.message ||
        'Something went wrong. Please try again.';

      setError(
        e?.response?.status === 404 ||
          msg.toLowerCase().includes('not found') ||
          msg.toLowerCase().includes('no account')
          ? 'No account found with that email address.'
          : msg,
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <Flex direction="column" style={{ width: '100%', maxWidth: '440px' }}>
      <AuthTitleSection />

      <form onSubmit={handleContinue}>
        <Flex direction="column" gap="4">
          <EmailField
            value={email}
            onChange={(v) => {
              setEmail(v);
              setError('');
            }}
            error={error}
            autoFocus
          />

          <LoadingButton
            type="submit"
            size="3"
            disabled={!email.trim()}
            loading={loading}
            loadingLabel="Checking…"
            style={{
              width: '100%',
              backgroundColor: email.trim() ? 'var(--accent-9)' : undefined,
              color: email.trim() ? 'white' : undefined,
              fontWeight: 500,
            }}
          >
            Continue
          </LoadingButton>
        </Flex>
      </form>
    </Flex>
  );
}
