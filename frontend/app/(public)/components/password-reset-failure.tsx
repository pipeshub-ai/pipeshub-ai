'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import Image from 'next/image';
import { Box, Flex, Text, Button } from '@radix-ui/themes';
import {
  PASSWORD_RESET_FAILURE_VARIANT,
  PASSWORD_RESET_FAILURE_MESSAGE_KEYS,
} from '../reset-password/constants';
import type { PasswordResetFailureProps } from '../reset-password/types';

/**
 * PasswordResetFailure — shown when the reset-password page cannot proceed:
 * no token in the URL, or the token has already expired (invite or reset).
 */
export default function PasswordResetFailure({
  variant = PASSWORD_RESET_FAILURE_VARIANT.MISSING_TOKEN,
}: PasswordResetFailureProps) {
  const router = useRouter();
  const { t } = useTranslation();
  const message = t(PASSWORD_RESET_FAILURE_MESSAGE_KEYS[variant]);

  return (
    <Box style={{ width: '100%', maxWidth: '440px' }}>
      <Box style={{ marginBottom: '24px' }}>
        <Image
          src="/login-page-assets/pipeshub/white-square.svg"
          alt="Pipeshub"
          width={48}
          height={48}
        />
      </Box>

      <Flex
        align="start"
        gap="2"
        style={{
          padding: '12px 14px',
          marginBottom: '20px',
          backgroundColor: 'var(--red-a3)',
          border: '1px solid var(--red-a6)',
          borderRadius: 'var(--radius-3)',
        }}
      >
        <span
          className="material-icons-outlined"
          style={{ fontSize: '18px', color: 'var(--red-11)', marginTop: '1px', flexShrink: 0 }}
        >
          error_outline
        </span>
        <Text size="2" style={{ color: 'var(--red-11)' }}>
          {message}
        </Text>
      </Flex>

      <Button
        size="3"
        variant="soft"
        style={{ width: '100%', cursor: 'pointer' }}
        onClick={() => router.push('/login')}
      >
        {t('resetPassword.failure.backToSignIn')}
      </Button>
    </Box>
  );
}
