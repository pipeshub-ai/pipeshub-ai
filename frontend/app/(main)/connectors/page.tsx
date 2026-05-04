'use client';

import { Flex, Text, Heading } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export default function ConnectorsPage() {
  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      style={{
        height: '100%',
        width: '100%',
        backgroundColor: 'var(--slate-2)',
      }}
    >
      <MaterialIcon name="hub" size={64} color="var(--slate-9)" />
      <Heading size="6" style={{ marginTop: '16px', color: 'var(--slate-12)' }}>
        Connectors
      </Heading>
      <Text size="2" style={{ marginTop: '8px', color: 'var(--slate-11)' }}>
        Coming soon
      </Text>
    </Flex>
  );
}
