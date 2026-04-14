'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import { Box, Flex, Text, Button } from '@radix-ui/themes';

/**
 * PasswordResetFailure — shown when the reset-password page is opened without
 * a valid token (e.g. navigated to directly, or link has expired).
 */
export default function PasswordResetFailure() {
  const router = useRouter();

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
          This page is only accessible from a password-reset email link. Please use the Forgot
          Password option on the sign-in page.
        </Text>
      </Flex>

      <Button
        size="3"
        variant="soft"
        style={{ width: '100%', cursor: 'pointer' }}
        onClick={() => router.push('/login')}
      >
        Back to Sign In
      </Button>
    </Box>
  );
}
