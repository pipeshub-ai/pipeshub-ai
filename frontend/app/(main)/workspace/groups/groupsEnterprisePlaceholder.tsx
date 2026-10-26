'use client';

import { Flex, Heading, Text } from '@radix-ui/themes';
import { pagePaddingX } from '@/app/components/ui/responsive';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { MOBILE_PAGE_PADDING_TOP } from '@/app/(main)/workspace/components/settings-page-container';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/**
 * Open-source Groups page — full group management is an Enterprise Edition feature.
 * Sidebar / bookmarks still land here so the upgrade message is visible.
 */
export default function GroupsEnterprisePlaceholder() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();

  return (
    <Flex
      direction="column"
      style={{
        height: '100%',
        width: '100%',
        paddingInline: pagePaddingX(40),
      }}
    >
      <Flex
        direction="column"
        gap="1"
        style={{ paddingTop: isMobile ? MOBILE_PAGE_PADDING_TOP : 64, paddingBottom: 24 }}
      >
        <Heading size="5" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('workspace.groups.title')}
        </Heading>
        <Text size="2" style={{ color: 'var(--slate-11)' }}>
          {t('workspace.groups.subtitle')}
        </Text>
      </Flex>

      <Flex
        direction="column"
        align="center"
        justify="center"
        gap="4"
        style={{
          flex: 1,
          minHeight: '400px',
          padding: 'var(--space-6)',
        }}
      >
        <Flex
          align="center"
          justify="center"
          style={{
            width: '64px',
            height: '64px',
            borderRadius: 'var(--radius-full)',
            backgroundColor: 'var(--slate-3)',
          }}
        >
          <MaterialIcon name="workspace_premium" size={28} color="var(--slate-9)" />
        </Flex>

        <Flex
          direction="column"
          align="center"
          gap="2"
          style={{ maxWidth: '720px', textAlign: 'center' }}
        >
          <Heading size="4" style={{ color: 'var(--slate-12)' }}>
            {t(
              'workspace.groups.enterpriseTitle',
              'Group permissions are available in the Enterprise Edition'
            )}
          </Heading>
          <Text size="2" style={{ color: 'var(--slate-11)', lineHeight: '20px' }}>
            {t(
              'workspace.groups.enterpriseMessage',
              'Upgrade to the Enterprise Edition to use group permissions.'
            )}
          </Text>
          <Text size="2" style={{ lineHeight: '20px' }}>
            <a
              href="https://docs.pipeshub.com/contact-us"
              target="_blank"
              rel="noreferrer"
              style={{ color: 'var(--accent-11)', textDecoration: 'none' }}
            >
              {t('workspace.groups.enterpriseContact')}
            </a>
          </Text>
        </Flex>
      </Flex>
    </Flex>
  );
}
