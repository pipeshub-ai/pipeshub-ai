'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Flex, Text, TextField, Button, Spinner } from '@radix-ui/themes';
import { useAuthStore } from '@/lib/store/auth-store';
import { isValidEmail, validatePassword } from '@/lib/utils/validators';
import { toast } from '@/lib/store/toast-store';
import { GuestGuard } from '@/app/components/ui/guest-guard';
import { useAuthWideLayout } from '@/lib/hooks/use-breakpoint';
import AuthHero from '../components/auth-hero';
import FormPanel from '../components/form-panel';
import AuthTitleSection from '../components/auth-title-section';
import { PasswordField } from '../forms/form-components';
import { AuthApi } from '../api';

// ─── Component ───────────────────────────────────────────────────────────────

export default function SignUpPage() {
  const router = useRouter();
  const splitLayout = useAuthWideLayout();
  const setTokens = useAuthStore((s) => s.setTokens);
  const setUser = useAuthStore((s) => s.setUser);

  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [registeredName, setRegisteredName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  function validate(): boolean {
    const next: Record<string, string> = {};

    if (!firstName.trim()) next.firstName = 'First name is required.';
    if (!lastName.trim()) next.lastName = 'Last name is required.';
    if (!registeredName.trim()) next.registeredName = 'Organization name is required.';

    if (!email.trim()) {
      next.email = 'Email is required.';
    } else if (!isValidEmail(email.trim())) {
      next.email = 'Please enter a valid email address.';
    }

    if (!password) {
      next.password = 'Password is required.';
    } else {
      const pwErr = validatePassword(password);
      if (pwErr) next.password = pwErr;
    }

    if (!confirmPassword) {
      next.confirmPassword = 'Please confirm your password.';
    } else if (password !== confirmPassword) {
      next.confirmPassword = 'Passwords do not match.';
    }

    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (loading) return;
    if (!validate()) return;

    setLoading(true);
    const toastId = toast.loading('Saving details...', {
      description: 'This will take a few seconds',
    });

    try {
      const response = await AuthApi.signUp({
        email: email.trim(),
        password,
        firstName: firstName.trim(),
        lastName: lastName.trim(),
        registeredName: registeredName.trim(),
      });

      setTokens(response.accessToken, response.refreshToken);
      if (response.user) setUser(response.user);
      toast.dismiss(toastId);
      toast.success('Account created successfully!');
      router.push('/onboarding');
    } catch (err: unknown) {
      type HttpErr = {
        response?: { data?: { error?: { message?: string }; message?: string }; status?: number };
        message?: string;
      };
      const error = err as HttpErr;
      const msg =
        error?.response?.data?.error?.message ??
        error?.response?.data?.message ??
        error?.message ??
        'Sign up failed. Please try again.';

      toast.dismiss(toastId);
      toast.error(msg);

      if (
        error?.response?.status === 409 ||
        msg.toLowerCase().includes('already exists') ||
        msg.toLowerCase().includes('already registered')
      ) {
        setErrors((prev) => ({ ...prev, email: 'An account with this email already exists.' }));
      }
    } finally {
      setLoading(false);
    }
  }

  const isFormFilled =
    firstName.trim() && lastName.trim() && registeredName.trim() && email.trim() && password && confirmPassword;

  return (
    <GuestGuard>
      <Flex
        direction={splitLayout ? 'row' : 'column'}
        style={{
          minHeight: '100dvh',
          overflow: splitLayout ? 'hidden' : undefined,
        }}
      >
        <AuthHero splitLayout={splitLayout} />
        <FormPanel splitLayout={splitLayout}>
          <Flex direction="column" style={{ width: '100%', maxWidth: '440px' }}>
            <AuthTitleSection
              title="Get Started"
              subtitle="Welcome to Pipeshub -- let's get started!"
            />

            <form onSubmit={handleSubmit}>
              <Flex direction="column" gap="4">
                {/* First Name */}
                <Flex direction="column" gap="1">
                  <Text
                    as="label"
                    htmlFor="first-name"
                    size="2"
                    style={{
                      color: 'var(--gray-12)',
                      fontWeight: 500,
                      lineHeight: '20px',
                    }}
                  >
                    First Name*
                  </Text>
                  <TextField.Root
                    id="first-name"
                    type="text"
                    value={firstName}
                    onChange={(e) => {
                      setFirstName(e.target.value);
                      setErrors((prev) => ({ ...prev, firstName: '' }));
                    }}
                    placeholder="Enter your first name"
                    autoComplete="given-name"
                    autoFocus
                    required
                    disabled={loading}
                    size="3"
                    color={errors.firstName ? 'red' : undefined}
                    style={{
                      width: '100%',
                      outline: errors.firstName ? '1px solid var(--red-8)' : undefined,
                    }}
                  />
                  {errors.firstName && (
                    <Text size="1" style={{ color: 'var(--red-11)', fontWeight: 500 }}>
                      {errors.firstName}
                    </Text>
                  )}
                </Flex>

                {/* Last Name */}
                <Flex direction="column" gap="1">
                  <Text
                    as="label"
                    htmlFor="last-name"
                    style={{
                      color: 'var(--gray-12)',
                      fontSize: '14px',
                      fontWeight: 500,
                      lineHeight: '20px',
                    }}
                  >
                    Last Name*
                  </Text>
                  <TextField.Root
                    id="last-name"
                    type="text"
                    value={lastName}
                    onChange={(e) => {
                      setLastName(e.target.value);
                      setErrors((prev) => ({ ...prev, lastName: '' }));
                    }}
                    placeholder="Enter your last name"
                    autoComplete="family-name"
                    required
                    disabled={loading}
                    size="3"
                    color={errors.lastName ? 'red' : undefined}
                    style={{
                      width: '100%',
                      outline: errors.lastName ? '1px solid var(--red-8)' : undefined,
                    }}
                  />
                  {errors.lastName && (
                    <Text size="1" style={{ color: 'var(--red-11)', fontWeight: 500 }}>
                      {errors.lastName}
                    </Text>
                  )}
                </Flex>

                {/* Organization Name */}
                <Flex direction="column" gap="1">
                  <Text
                    as="label"
                    htmlFor="registered-name"
                    style={{
                      color: 'var(--gray-12)',
                      fontSize: '14px',
                      fontWeight: 500,
                      lineHeight: '20px',
                    }}
                  >
                    Organization Name*
                  </Text>
                  <TextField.Root
                    id="registered-name"
                    type="text"
                    value={registeredName}
                    onChange={(e) => {
                      setRegisteredName(e.target.value);
                      setErrors((prev) => ({ ...prev, registeredName: '' }));
                    }}
                    placeholder="Enter your organization name"
                    autoComplete="organization"
                    required
                    disabled={loading}
                    size="3"
                    color={errors.registeredName ? 'red' : undefined}
                    style={{
                      width: '100%',
                      outline: errors.registeredName ? '1px solid var(--red-8)' : undefined,
                    }}
                  />
                  {errors.registeredName && (
                    <Text size="1" style={{ color: 'var(--red-11)', fontWeight: 500 }}>
                      {errors.registeredName}
                    </Text>
                  )}
                </Flex>

                {/* Email */}
                <Flex direction="column" gap="1">
                  <Text
                    as="label"
                    htmlFor="signup-email"
                    style={{
                      color: 'var(--gray-12)',
                      fontSize: '14px',
                      fontWeight: 500,
                      lineHeight: '20px',
                    }}
                  >
                    Your Email*
                  </Text>
                  <TextField.Root
                    id="signup-email"
                    type="text"
                    inputMode="email"
                    value={email}
                    onChange={(e) => {
                      setEmail(e.target.value);
                      setErrors((prev) => ({ ...prev, email: '' }));
                    }}
                    placeholder="Enter your email"
                    autoComplete="email"
                    required
                    disabled={loading}
                    size="3"
                    color={errors.email ? 'red' : undefined}
                    style={{
                      width: '100%',
                      outline: errors.email ? '1px solid var(--red-8)' : undefined,
                    }}
                  />
                  {errors.email && (
                    <Text size="1" style={{ color: 'var(--red-11)', fontWeight: 500 }}>
                      {errors.email}
                    </Text>
                  )}
                </Flex>

                {/* Password */}
                <PasswordField
                  value={password}
                  onChange={(v) => {
                    setPassword(v);
                    setErrors((prev) => ({ ...prev, password: '' }));
                  }}
                  label="Password*"
                  placeholder="Enter your password"
                  error={errors.password}
                  hint="At least 8 characters: lowercase, uppercase, number, symbol."
                  autoComplete="new-password"
                  id="signup-password"
                  disabled={loading}
                />

                {/* Confirm Password */}
                <PasswordField
                  value={confirmPassword}
                  onChange={(v) => {
                    setConfirmPassword(v);
                    setErrors((prev) => ({ ...prev, confirmPassword: '' }));
                  }}
                  label="Confirm Password*"
                  placeholder="Confirm your password"
                  error={errors.confirmPassword}
                  autoComplete="new-password"
                  id="signup-confirm-password"
                  disabled={loading}
                />

                {/* Submit */}
                <Button
                  type="submit"
                  size="3"
                  disabled={loading || !isFormFilled}
                  style={{
                    width: '100%',
                    backgroundColor: isFormFilled && !loading ? 'var(--accent-9)' : undefined,
                    color: isFormFilled && !loading ? 'white' : undefined,
                    fontWeight: 500,
                    cursor: loading ? 'wait' : 'pointer',
                  }}
                >
                  {loading ? <Spinner size="2" /> : 'Continue'}
                </Button>
              </Flex>
            </form>
          </Flex>
        </FormPanel>
      </Flex>
    </GuestGuard>
  );
}
