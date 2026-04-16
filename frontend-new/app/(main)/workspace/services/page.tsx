'use client';

import { Flex, Heading, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export default function ServicesPage() {
  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      style={{ height: '100%', width: '100%' }}
    >
      <MaterialIcon name="manage_accounts" size={48} color="var(--slate-9)" />
      <Heading size="5" style={{ marginTop: 'var(--space-4)', color: 'var(--slate-12)' }}>
        Services
      </Heading>
      <Text size="2" style={{ marginTop: 'var(--space-2)', color: 'var(--slate-11)' }}>
        Service settings — coming soon.
      </Text>
    </Flex>
  );
}
