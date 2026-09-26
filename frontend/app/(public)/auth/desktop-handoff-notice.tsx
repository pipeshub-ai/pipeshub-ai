'use client';

import { Button, Flex, Heading, Text } from '@radix-ui/themes';

/**
 * Shown once an OAuth result has been handed to the PipesHub desktop app.
 *
 * The automatic navigation to the `pipeshub://` link is best effort: browsers
 * prompt before handing a custom scheme to an application, and some suppress
 * the navigation entirely when it is not driven by a user gesture. The link
 * here is the guaranteed path, so it is never hidden behind a delay.
 */
export default function DesktopHandoffNotice({ deepLink }: { deepLink: string }) {
  return (
    <Flex
      align="center"
      justify="center"
      direction="column"
      gap="3"
      style={{ height: '100vh', padding: 'var(--space-4)', textAlign: 'center' }}
    >
      <Heading size="4">Sign-in sent to PipesHub</Heading>
      <Text size="2" color="gray">
        You can close this tab and return to the PipesHub app.
      </Text>
      <Button asChild size="2" variant="soft">
        <a href={deepLink}>Open PipesHub</a>
      </Button>
    </Flex>
  );
}
