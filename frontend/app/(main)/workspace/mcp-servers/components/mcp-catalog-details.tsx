'use client';

import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Flex, Link, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { WorkspaceRightPanel } from '../../components/workspace-right-panel';
import { categoryIcon } from '../categories';
import type { McpServerTemplate } from '../types';
import { McpServerIcon } from './mcp-server-icon';

interface McpCatalogDetailsProps {
  template: McpServerTemplate | null;
  /** How many servers this person already has from it. */
  addedCount: number;
  /** Adding makes an org server (admins) or the person's own. */
  isAdmin: boolean;
  onClose: () => void;
  onAdd: (template: McpServerTemplate) => void;
}

const hostOf = (url: string) => {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
};

/** One catalog entry: what it can do, whether it changes data, how people sign in, and Add. */
export function McpCatalogDetails({ template, addedCount, isAdmin, onClose, onAdd }: McpCatalogDetailsProps) {
  const { t } = useTranslation();
  if (!template) return null;
  const capabilities = template.capabilities ?? [];
  const signIn =
    template.defaultAuthMode === 'oauth'
      ? t('workspace.mcpServers.discover.signInOauth', {
          name: template.displayName,
        })
      : template.defaultAuthMode === 'none'
        ? t('workspace.mcpServers.discover.signInNone')
        : t('workspace.mcpServers.discover.signInToken');
  const section = (title: string, children: ReactNode) => (
    <Flex direction="column" gap="2">
      <Text
        size="1"
        weight="bold"
        style={{
          color: 'var(--gray-10)',
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
        }}
      >
        {title}
      </Text>
      {children}
    </Flex>
  );

  return (
    <WorkspaceRightPanel
      open
      onOpenChange={(open) => !open && onClose()}
      title={template.displayName}
      icon={<McpServerIcon typeId={template.typeId} name={template.displayName} size={20} color="var(--gray-11)" />}
      primaryLabel={
        addedCount > 0
          ? t('workspace.mcpServers.catalog.addAnother')
          : isAdmin
            ? t('workspace.mcpServers.discover.addOrg')
            : t('workspace.mcpServers.discover.addPersonal')
      }
      onPrimaryClick={() => onAdd(template)}
      secondaryLabel={t('common.close')}
      onSecondaryClick={onClose}
    >
      <Flex direction="column" gap="5" data-testid="mcp-catalog-details">
        <Flex direction="column" gap="2">
          <Flex gap="2" wrap="wrap">
            {template.category && (
              <Badge color="gray" variant="soft">
                <MaterialIcon name={categoryIcon(template.category)} size={13} color="currentColor" />
                {t(`workspace.mcpServers.categories.${template.category}`)}
              </Badge>
            )}
            {addedCount > 0 && (
              <Badge color="green" variant="soft">
                <MaterialIcon name="check" size={13} color="currentColor" />
                {t('workspace.mcpServers.discover.added', {
                  count: addedCount,
                })}
              </Badge>
            )}
          </Flex>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {template.description}
          </Text>
        </Flex>

        {capabilities.length > 0 &&
          section(
            t('workspace.mcpServers.discover.whatItDoes'),
            <Flex as="div" direction="column" gap="2" role="list">
              {capabilities.map((cap) => (
                <Flex key={cap} role="listitem" align="start" gap="2">
                  <MaterialIcon name="check_circle" size={17} color="var(--accent-11)" />
                  <Text
                    size="2"
                    weight={cap === template.matchedCapability ? 'bold' : 'regular'}
                    style={{ color: 'var(--gray-12)' }}
                  >
                    {cap}
                  </Text>
                </Flex>
              ))}
            </Flex>,
          )}

        {section(
          t('workspace.mcpServers.discover.beforeYouAdd'),
          <Flex direction="column" gap="2">
            <Flex>
              <Badge color="gray" variant="soft">
                <MaterialIcon name={template.readOnly ? 'visibility' : 'edit'} size={13} color="currentColor" />
                {template.readOnly
                  ? t('workspace.mcpServers.discover.readOnly')
                  : t('workspace.mcpServers.discover.changesData')}
              </Badge>
            </Flex>
            <Text size="2" style={{ color: 'var(--gray-11)' }}>
              {template.readOnly
                ? t('workspace.mcpServers.discover.readOnlyNote')
                : t('workspace.mcpServers.discover.changesDataNote')}
            </Text>
          </Flex>,
        )}

        <Flex direction="column" gap="2">
          <Flex gap="3">
            <Text size="2" style={{ color: 'var(--gray-10)', width: 120, flexShrink: 0 }}>
              {t('workspace.mcpServers.discover.signIn')}
            </Text>
            <Text size="2" style={{ color: 'var(--gray-12)', minWidth: 0 }}>
              {signIn}
            </Text>
          </Flex>
          {template.documentationUrl && (
            <Flex gap="3">
              <Text size="2" style={{ color: 'var(--gray-10)', width: 120, flexShrink: 0 }}>
                {t('workspace.mcpServers.discover.documentation')}
              </Text>
              <Link
                size="2"
                href={template.documentationUrl}
                target="_blank"
                rel="noopener noreferrer"
                style={{ overflowWrap: 'anywhere' }}
              >
                {hostOf(template.documentationUrl)}
              </Link>
            </Flex>
          )}
        </Flex>

        <Text size="2" style={{ color: 'var(--gray-11)' }}>
          {isAdmin ? t('workspace.mcpServers.discover.orgNote') : t('workspace.mcpServers.discover.personalNote')}
        </Text>
      </Flex>
    </WorkspaceRightPanel>
  );
}
