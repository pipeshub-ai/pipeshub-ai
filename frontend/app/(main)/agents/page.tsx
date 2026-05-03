'use client';

import { Flex, Text, Heading } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export default function AgentsPage() {
  const { t } = useTranslation();
  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      style={{
        height: '100%',
        width: '100%',
        backgroundColor: 'var(--olive-2)',
      }}
    >
      <MaterialIcon name="memory" size={64} color="var(--olive-9)" />
      <Heading size="6" style={{ marginTop: '16px', color: 'var(--olive-12)' }}>
        {t('agents.pageTitle')}
      </Heading>
      <Text size="2" style={{ marginTop: '8px', color: 'var(--olive-11)' }}>
        {t('agents.comingSoon')}
      </Text>
    </Flex>
  );
}
