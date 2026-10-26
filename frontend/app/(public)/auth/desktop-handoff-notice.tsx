'use client';

import { Button, Flex, Heading, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

/**
 * Shown once an OAuth result has been handed to the PipesHub desktop app.
 *
 * The automatic navigation to the `pipeshub://` link is best effort: browsers
 * prompt before handing a custom scheme to an application, and some suppress
 * the navigation entirely when it is not driven by a user gesture. The link
 * here is the guaranteed path, so it is never hidden behind a delay.
 */
export default function DesktopHandoffNotice({
  deepLink,
  titleKey = 'auth.desktopHandoff.title',
}: {
  deepLink: string;
  /** i18n key for the heading; sign-in by default, integration callbacks pass their own. */
  titleKey?: string;
}) {
  const { t } = useTranslation();

  return (
    <Flex
      align="center"
      justify="center"
      direction="column"
      gap="3"
      style={{ height: '100vh', padding: 'var(--space-4)', textAlign: 'center' }}
    >
      <Heading size="4">{t(titleKey)}</Heading>
      <Text size="2" color="gray">
        {t('auth.desktopHandoff.description')}
      </Text>
      <Button asChild size="2" variant="soft">
        <a href={deepLink}>{t('auth.desktopHandoff.openApp')}</a>
      </Button>
    </Flex>
  );
}
