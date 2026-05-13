'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Flex, Text, TextField, Button, Spinner, Select, AlertDialog, Box } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/lib/store/auth-store';
import { isValidEmail, validatePassword } from '@/lib/utils/validators';
import { toast } from '@/lib/store/toast-store';
import { GuestGuard } from '@/app/components/ui/guest-guard';
import { LoadingScreen } from '@/app/components/ui/auth-guard';
import { getOrgExists } from '@/lib/api/org-exists-public';
import { useAuthWideLayout } from '@/lib/hooks/use-breakpoint';
import { useLanguageStore, type Language } from '@/lib/store/language-store';
import { SUPPORTED_LANGUAGES } from '@/lib/i18n/supported-languages';
import AuthHero from '../components/auth-hero';
import FormPanel from '../components/form-panel';
import AuthTitleSection from '../components/auth-title-section';
import { PasswordField } from '../forms/form-components';
import { AuthApi } from '../api';

const LANGUAGES = Object.values(SUPPORTED_LANGUAGES) as { value: Language; menuName: string }[];

// ─── Component ───────────────────────────────────────────────────────────────

export default function SignUpPage() {
  const router = useRouter();
  const splitLayout = useAuthWideLayout();
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const setTokens = useAuthStore((s) => s.setTokens);
  const setUser = useAuthStore((s) => s.setUser);

  const { language, setLanguage } = useLanguageStore();
  const { t } = useTranslation();

  const [orgAllowsSignUp, setOrgAllowsSignUp] = useState(false);

  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [registeredName, setRegisteredName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({}); 
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);

  useEffect(() => {
    if (!isHydrated) return;
    let cancelled = false;

    void getOrgExists()
      .then(({ exists }) => {
        if (cancelled) return;
        if (exists) {
          router.replace('/login');
          return;
        }
        setOrgAllowsSignUp(true);
      })
      .catch(() => {
        if (cancelled) return;
        setOrgAllowsSignUp(true);
      });

    return () => {
      cancelled = true;
    };
  }, [isHydrated, router]);

  function validate(): boolean {
    const next: Record<string, string> = {};

    if (!firstName.trim()) next.firstName = t('auth.signUp.errors.firstNameRequired');
    if (!lastName.trim()) next.lastName = t('auth.signUp.errors.lastNameRequired');
    if (!registeredName.trim()) next.registeredName = t('auth.signUp.errors.orgNameRequired');

    if (!email.trim()) {
      next.email = t('auth.signUp.errors.emailRequired');
    } else if (!isValidEmail(email.trim())) {
      next.email = t('auth.signUp.errors.emailInvalid');
    }

    if (!password) {
      next.password = t('auth.signUp.errors.passwordRequired');
    } else {
      const pwErr = validatePassword(password);
      if (pwErr) next.password = pwErr;
    }

    if (!confirmPassword) {
      next.confirmPassword = t('auth.signUp.errors.confirmPasswordRequired');
    } else if (password !== confirmPassword) {
      next.confirmPassword = t('auth.signUp.errors.passwordsMismatch');
    }

    setErrors(next);
    return Object.keys(next).length === 0;
  }

  function handleFormSubmit(e: React.FormEvent) {
    e.preventDefault();   
    if (!validate()) return;
    setShowConfirmDialog(true);
  }

  async function handleConfirmSignUp() {
    if (loading) return;
    setLoading(true);
    const toastId = toast.loading(t('auth.signUp.savingToast'), {
      description: t('auth.signUp.savingToastDescription'),
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
      toast.success(t('auth.signUp.successToast'));
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
        t('auth.signUp.failToast');

      toast.dismiss(toastId);
      toast.error(msg);

      if (
        error?.response?.status === 409 ||
        msg.toLowerCase().includes('already exists') ||
        msg.toLowerCase().includes('already registered')
      ) {
        setErrors((prev) => ({ ...prev, email: t('auth.signUp.errors.emailAlreadyExists') }));
      }
    } finally {
      setLoading(false);
    }
  }

  const isFormFilled =
    firstName.trim() && lastName.trim() && registeredName.trim() && email.trim() && password && confirmPassword;

  return (
    <>
    <GuestGuard>
      {!orgAllowsSignUp ? (
        <LoadingScreen />
      ) : (
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
              title={t('auth.signUp.title')}
              subtitle={t('auth.signUp.subtitle')}
            />

            <form onSubmit={handleFormSubmit}>
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
                    {t('auth.signUp.firstNameLabel')}
                  </Text>
                  <TextField.Root
                    id="first-name"
                    type="text"
                    value={firstName}
                    onChange={(e) => {
                      setFirstName(e.target.value);
                      setErrors((prev) => ({ ...prev, firstName: '' }));
                    }}
                    placeholder={t('auth.signUp.firstNamePlaceholder')}
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
                    {t('auth.signUp.lastNameLabel')}
                  </Text>
                  <TextField.Root
                    id="last-name"
                    type="text"
                    value={lastName}
                    onChange={(e) => {
                      setLastName(e.target.value);
                      setErrors((prev) => ({ ...prev, lastName: '' }));
                    }}
                    placeholder={t('auth.signUp.lastNamePlaceholder')}
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
                    {t('auth.signUp.orgNameLabel')}
                  </Text>
                  <TextField.Root
                    id="registered-name"
                    type="text"
                    value={registeredName}
                    onChange={(e) => {
                      setRegisteredName(e.target.value);
                      setErrors((prev) => ({ ...prev, registeredName: '' }));
                    }}
                    placeholder={t('auth.signUp.orgNamePlaceholder')}
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
                    {t('auth.signUp.emailLabel')}
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
                    placeholder={t('auth.signUp.emailPlaceholder')}
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
                  label={t('auth.signUp.passwordLabel')}
                  placeholder={t('auth.signUp.passwordPlaceholder')}
                  error={errors.password}
                  hint={t('auth.signUp.passwordHint')}
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
                  label={t('auth.signUp.confirmPasswordLabel')}
                  placeholder={t('auth.signUp.confirmPasswordPlaceholder')}
                  error={errors.confirmPassword}
                  autoComplete="new-password"
                  id="signup-confirm-password"
                  disabled={loading}
                />

                {/* Language */}
                <Flex direction="column" gap="1">
                  <Text
                    as="label"
                    size="2"
                    style={{ color: 'var(--gray-12)', fontWeight: 500, lineHeight: '20px' }}
                  >
                    {t('auth.signUp.preferredLanguageLabel')}
                  </Text>
                  <Select.Root
                    value={language}
                    onValueChange={(val) => setLanguage(val as Language)}
                    size="3"
                    disabled={loading}
                  >
                    <Select.Trigger style={{ width: '100%' }} />
                    <Select.Content>
                      {LANGUAGES.map((lang) => (
                        <Select.Item key={lang.value} value={lang.value}>
                          {lang.menuName}
                        </Select.Item>
                      ))}
                    </Select.Content>
                  </Select.Root>
                </Flex>

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
                  {loading ? <Spinner size="2" /> : t('auth.signUp.continueButton')}
                </Button>
              </Flex>
            </form>
          </Flex>
        </FormPanel>
      </Flex>
      )}
    </GuestGuard>

    <AlertDialog.Root open={showConfirmDialog} onOpenChange={(v) => !loading && setShowConfirmDialog(v)}>
      <AlertDialog.Content
        style={{
          maxWidth: '34rem',
          padding: 'var(--space-5)',
          backgroundColor: 'var(--color-panel-solid)',
          borderRadius: 'var(--radius-5)',
          border: '1px solid var(--olive-a3)',
          boxShadow:
            '0 16px 36px -20px rgba(0, 6, 46, 0.2), 0 16px 64px rgba(0, 0, 85, 0.02), 0 12px 60px rgba(0, 0, 0, 0.15)',
        }}
      >
        <AlertDialog.Title style={{ color: 'var(--slate-12)', fontSize: '1rem', fontWeight: 600 }}>
          {t('auth.signUp.confirmDialog.title')}
        </AlertDialog.Title>

        <AlertDialog.Description size="2" style={{ color: 'var(--slate-11)', lineHeight: '20px', marginTop: 8 }}>
          {t('auth.signUp.confirmDialog.description')}
        </AlertDialog.Description>

        <Flex direction="column" mt="3" gap="3">
          <Box
            style={{
              background: 'var(--olive-2)',
              border: '1px solid var(--olive-4)',
              borderRadius: 'var(--radius-2)',
              padding: 'var(--space-3)',
            }}
          >
            <Text size="2" weight="bold" style={{ color: 'var(--slate-12)', display: 'block', marginBottom: 'var(--space-2)' }}>
              {t('auth.signUp.confirmDialog.permissionsTitle')}
            </Text>
            <Text size="2" style={{ color: 'var(--slate-11)', lineHeight: '20px' }}>
              {t('auth.signUp.confirmDialog.permissionsBody')}
            </Text>
          </Box>

          <Text size="2" style={{ color: 'var(--slate-11)', lineHeight: '20px' }}>
            {t('auth.signUp.confirmDialog.emailNotePre')}{' '}
            <Text size="2" weight="bold" style={{ color: 'var(--slate-12)' }}>{email.trim()}</Text>{' '}
            {t('auth.signUp.confirmDialog.emailNotePost')}
          </Text>
        </Flex>

        <Flex justify="end" gap="2" mt="5">
          <AlertDialog.Cancel>
            <Button
              type="button"
              variant="outline"
              color="gray"
              size="2"
              style={{ cursor: 'pointer' }}
            >
              {t('auth.signUp.confirmDialog.cancelButton')}
            </Button>
          </AlertDialog.Cancel>
          <AlertDialog.Action>
            <Button
              type="button"
              size="2"
              disabled={loading}
              style={{
                backgroundColor: 'var(--accent-9)',
                color: 'white',
                cursor: loading ? 'wait' : 'pointer',
                fontWeight: 500,
              }}
              onClick={() => void handleConfirmSignUp()}
            >
              {loading ? <Spinner size="2" /> : t('auth.signUp.confirmDialog.signUpButton')}
            </Button>
          </AlertDialog.Action>
        </Flex>
      </AlertDialog.Content>
    </AlertDialog.Root>
    </>
  );
}
