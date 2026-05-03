'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, Switch } from '@radix-ui/themes';

interface JitFieldProps {
  providerName: string;
  checked: boolean;
  onCheckedChange: (val: boolean) => void;
}

export function JitField({ providerName, checked, onCheckedChange }: JitFieldProps) {
  const { t } = useTranslation();
  return (
    <Box
      style={{
        border: '1px solid var(--slate-5)',
        borderRadius: 'var(--radius-2)',
        padding: '12px 14px',
      }}
    >
      <Flex align="center" justify="between">
        <Box>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
            {t('workspace.authentication.jit.title')}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-10)', display: 'block', marginTop: 2 }}>
            {t('workspace.authentication.jit.description', { providerName })}
          </Text>
        </Box>
        <Switch
          checked={checked}
          onCheckedChange={onCheckedChange}
          style={{ flexShrink: 0, marginLeft: 'var(--space-4)', cursor: 'pointer' }}
        />
      </Flex>
    </Box>
  );
}
