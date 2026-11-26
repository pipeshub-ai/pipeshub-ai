'use client';

import { Button, Flex, Text } from '@radix-ui/themes';
import { Link } from '@/lib/navigation';

/** An empty, loading or failed state: one plain sentence and, where there is somewhere to go, one way on. */
export function JobsMessage({
  text,
  busy,
  action,
  link,
}: {
  text: string;
  busy?: boolean;
  action?: { label: string; onClick: () => void };
  link?: { label: string; href: string };
}) {
  return (
    <Flex direction="column" align="center" gap="3" role={busy ? 'status' : undefined} style={{ padding: 'var(--space-8) var(--space-4)', textAlign: 'center' }}>
      <Text size="2" color="gray" style={{ maxWidth: 420 }}>
        {text}
      </Text>
      {action && (
        <Button size="2" variant="soft" color="gray" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
      {link && (
        <Button asChild size="2" variant="soft" color="gray">
          <Link href={link.href}>{link.label}</Link>
        </Button>
      )}
    </Flex>
  );
}
