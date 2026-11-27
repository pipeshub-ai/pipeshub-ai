'use client';

import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, IconButton, Text } from '@radix-ui/themes';
import { Link } from '@/lib/navigation';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { ENTERPRISE_OVERVIEW_ROUTE } from '@/lib/constants/enterprise';
import { useEnterprisePromptsStore } from '@/app/(main)/workspace/enterprise/store';
import { WorkspaceSidebarItem } from './sidebar-item';

interface EnterpriseSidebarEntryProps {
  isActive: boolean;
}

/**
 * The way into the Enterprise Edition page from every settings page: a small card under the
 * list, or, once the admin closes it, a one-line link in its place so the page stays findable.
 */
export function EnterpriseSidebarEntry({ isActive }: EnterpriseSidebarEntryProps) {
  const { t } = useTranslation();
  const dismissed = useEnterprisePromptsStore((s) => s.sidebarCardDismissed);
  const dismiss = useEnterprisePromptsStore((s) => s.dismissSidebarCard);
  const href = `${ENTERPRISE_OVERVIEW_ROUTE}/`;

  if (dismissed) {
    return (
      // The border sets the link apart from the list scrolling behind it.
      <Box style={{ padding: 'var(--space-2)', borderTop: '1px solid var(--olive-3)' }}>
        <WorkspaceSidebarItem
          icon={<MaterialIcon name="workspace_premium" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />}
          label={t('workspace.sidebar.nav.enterprise')}
          href={href}
          isActive={isActive}
        />
      </Box>
    );
  }

  return (
    <Box style={{ padding: 'var(--space-2)' }}>
      <Flex
        direction="column"
        gap="2"
        data-testid="enterprise-sidebar-card"
        style={{
          padding: 'var(--space-3)',
          border: '1px solid var(--accent-a5)',
          borderRadius: 'var(--radius-3)',
          background: 'var(--accent-a2)',
        }}
      >
        <Flex align="center" gap="2">
          <span aria-hidden="true" style={{ display: 'inline-flex', flexShrink: 0 }}>
            <MaterialIcon name="workspace_premium" size={18} color="var(--accent-11)" />
          </span>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', flex: 1, minWidth: 0 }}>
            {t('workspace.enterprise.sidebarCard.title')}
          </Text>
          <IconButton
            size="1"
            variant="ghost"
            color="gray"
            className="touch-hit"
            aria-label={t('workspace.enterprise.sidebarCard.dismiss')}
            onClick={dismiss}
          >
            <MaterialIcon name="close" size={16} color="currentColor" />
          </IconButton>
        </Flex>
        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {t('workspace.enterprise.sidebarCard.body')}
        </Text>
        <Button asChild size="1" className="touch-hit" style={{ alignSelf: 'flex-start' }}>
          <Link href={href}>{t('workspace.enterprise.sidebarCard.action')}</Link>
        </Button>
      </Flex>
    </Box>
  );
}
