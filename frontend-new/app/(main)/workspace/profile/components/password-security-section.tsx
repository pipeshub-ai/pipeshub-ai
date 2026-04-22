'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SettingsSection } from './settings-section';

// ========================================
// Types
// ========================================

export interface PasswordSecuritySectionProps {
  onChangePasswordClick: () => void;
}

// ========================================
// Component
// ========================================

export function PasswordSecuritySection({ onChangePasswordClick }: PasswordSecuritySectionProps) {
  const { t } = useTranslation();
  const [btnHovered, setBtnHovered] = useState(false);

  return (
    <SettingsSection title={t('workspace.profile.passwordSecurity.title')}>
      <Flex align="center" justify="between" style={{ width: '100%' }}>

        {/* Left: icon + label + description */}
        <Flex align="center" gap="3">
          <Flex
            align="center"
            justify="center"
            style={{
              width: 36,
              height: 36,
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'var(--gray-4)',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name="lock" size={18} color="var(--gray-11)" />
          </Flex>
          <Box>
            <Text
              size="2"
              weight="medium"
              style={{ color: 'var(--gray-12)', display: 'block' }}
            >
              {t('workspace.profile.passwordSecurity.accountPassword')}
            </Text>
            <Text
              size="1"
              style={{
                color: 'var(--gray-9)',
                display: 'block',
                marginTop: 2,
                lineHeight: '16px',
                fontWeight: 300,
              }}
            >
              {t('workspace.profile.passwordSecurity.followEmailDescription')}
            </Text>
          </Box>
        </Flex>

        <Button
          type="button"
          onClick={onChangePasswordClick}
          onMouseEnter={() => setBtnHovered(true)}
          onMouseLeave={() => setBtnHovered(false)}
          variant='solid'
          size='2'
          style={{
            backgroundColor: btnHovered ? 'var(--slate-a4)' : 'var(--slate-a3)',
          }}
        >
          {t('workspace.profile.passwordSecurity.changePassword')}
        </Button>

      </Flex>
    </SettingsSection>
  );
}
