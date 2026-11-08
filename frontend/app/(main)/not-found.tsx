'use client';

import Link from 'next/link';
import { Button, Flex, Heading, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { BrandMark } from '@/config';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { touchTarget } from '@/app/components/ui/responsive';

export default function NotFound() {
  const { t } = useTranslation();
  const isBelowDesktop = useIsBelowDesktop();
  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="3"
      className="viewport-height"
      style={{ padding: 'var(--space-5)', textAlign: 'center' }}
    >
      <BrandMark size={40} color="var(--accent-8)" />
      <Heading size="5">{t('common.errorBoundary.notFoundTitle')}</Heading>
      <Text size="2" color="gray">{t('common.errorBoundary.notFoundBody')}</Text>
      <Button size="3" asChild style={touchTarget(isBelowDesktop)}>
        <Link href="/chat/">{t('common.errorBoundary.goToChat')}</Link>
      </Button>
    </Flex>
  );
}
