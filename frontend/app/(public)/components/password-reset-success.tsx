'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import Image from 'next/image';
import { Box, Flex, Text, Button } from '@radix-ui/themes';

/**
 * PasswordResetSuccess — shown after the user successfully sets a new password.
 * Prompts them to sign in with the new credentials.
 */
export default function PasswordResetSuccess() {
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

      <Flex direction="column" gap="3" style={{ marginBottom: '32px' }}>
        <Text
          style={{
            color: 'var(--gray-12)',
            fontSize: '24px',
            fontWeight: 500,
            letterSpacing: '-0.1px',
            lineHeight: '30px',
          }}
        >
          Your password has been changed!
        </Text>
        <Text style={{ color: 'var(--gray-11)', fontSize: '14px', lineHeight: '20px' }}>
          Your password has been successfully updated. You can now sign in to your Pipeshub
          workspace with your new password.
        </Text>
      </Flex>

      <Button
        size="3"
        style={{
          width: '100%',
          backgroundColor: 'var(--accent-9)',
          color: 'white',
          fontWeight: 500,
          cursor: 'pointer',
        }}
        onClick={() => router.push('/login')}
      >
        Sign In
      </Button>
    </Box>
  );
}
