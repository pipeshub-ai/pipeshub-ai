'use client';

import React from 'react';
import Image from 'next/image';
import { LoadingButton } from '@/app/components/ui/loading-button';

// ─── Types ────────────────────────────────────────────────────────────────────

export type AuthProviderType = 'google' | 'microsoft' | 'github';

const PROVIDER_CONFIG: Record<
  AuthProviderType,
  { label: string; icon: React.ReactNode }
> = {
  google: {
    label: 'Continue with Google',
    icon: (
      <Image
        src="/login-page-assets/providers/google-fill.svg"
        alt="Google"
        width={18}
        height={18}
      />
    ),
  },
  microsoft: {
    label: 'Continue with Microsoft',
    icon: (
      <Image
        src="/login-page-assets/providers/microsoft.svg"
        alt="Microsoft"
        width={18}
        height={18}
      />
    ),
  },
  github: {
    label: 'Continue with GitHub',
    icon: (
      <Image
        src="/icons/auth-config/github.svg"
        alt="GitHub"
        width={18}
        height={18}
      />
    ),
  },
};

// ─── Props ────────────────────────────────────────────────────────────────────

export interface ProviderButtonProps {
  provider: AuthProviderType;
  onClick: () => void;
  disabled?: boolean;
  loading?: boolean;
  /** Render as accent-filled primary button instead of outline. */
  primary?: boolean;
}

// ─── Component ────────────────────────────────────────────────────────────────

/**
 * ProviderButton — a single configurable button for a sign-in provider,
 * showing its logo. Default style is outline; set `primary` for accent-filled.
 */
export default function ProviderButton({
  provider,
  onClick,
  disabled = false,
  loading = false,
  primary = false,
}: ProviderButtonProps) {
  const { icon, label } = PROVIDER_CONFIG[provider];
  return (
    <LoadingButton
      type="button"
      size="3"
      variant={primary ? 'solid' : 'outline'}
      disabled={disabled}
      loading={loading}
      loadingLabel="Connecting…"
      style={{
        width: '100%',
        fontWeight: 500,
        ...(primary
          ? { backgroundColor: 'var(--accent-9)', color: 'white' }
          : { color: 'var(--gray-12)', borderColor: 'var(--gray-a6)' }),
      }}
      onClick={onClick}
    >
      {icon}
      {label}
    </LoadingButton>
  );
}
