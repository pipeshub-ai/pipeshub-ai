'use client';

import React, { useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui';
import { ThemeableAssetIcon } from '@/app/components/ui/themeable-asset-icon';
import { getConnectorIconPath } from '@/lib/utils/connector-icon-utils';
import type { DocumentationLink } from '../../types';

// ========================================
// DocumentationSection
// ========================================

export function DocumentationSection({
  links,
  /** Same as connector config panel header — includes dark-mode invert for logos like Linear. */
  connectorType,
  /** When `connectorType` is omitted (e.g. workspace actions), vendor rows use this asset path. */
  connectorIconPath,
}: {
  links: DocumentationLink[];
  connectorType?: string;
  connectorIconPath?: string;
}) {
  const { t } = useTranslation();
  const iconSrc = connectorIconPath ? getConnectorIconPath(connectorIconPath) : undefined;

  return (
    <Flex direction="column" gap="3">
      <Flex direction="column" gap="1">
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {t('workspace.connectors.docSection.title')}
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          {t('workspace.connectors.docSection.description')}
        </Text>
      </Flex>

      <Flex direction="column" gap="2">
        {links.map((link, idx) => (
          <DocumentationLinkRow
            key={idx}
            link={link}
            connectorType={connectorType}
            connectorIconSrc={iconSrc}
          />
        ))}
      </Flex>
    </Flex>
  );
}

// ========================================
// DocumentationLinkRow
// ========================================

const PIPESHUB_ICON_PATH = '/logo/pipes-hub.svg';

function DocumentationLinkRow({
  link,
  connectorType,
  connectorIconSrc,
}: {
  link: DocumentationLink;
  connectorType?: string;
  connectorIconSrc?: string;
}) {
  const [isHovered, setIsHovered] = useState(false);

  const isPipeshub = link.type?.toLowerCase() === 'pipeshub';

  const handleClick = useCallback(() => {
    window.open(link.url, '_blank', 'noopener,noreferrer');
  }, [link.url]);

  return (
    <Flex
      align="center"
      justify="between"
      onClick={handleClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        backgroundColor: isHovered ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-2) var(--space-2) var(--space-2) var(--space-3)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <Flex align="center" gap="2">
        {isPipeshub ? (
          <ThemeableAssetIcon
            src={PIPESHUB_ICON_PATH}
            size={16}
            color="var(--gray-11)"
            variant="flat"
          />
        ) : connectorType ? (
          <ConnectorIcon type={connectorType} size={16} />
        ) : connectorIconSrc ? (
          <ThemeableAssetIcon
            src={connectorIconSrc}
            size={16}
            color="var(--gray-11)"
            variant="flat"
          />
        ) : (
          <ConnectorIcon type="generic" size={16} />
        )}
        <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }}>
          {link.title}
        </Text>
      </Flex>

      <Flex
        align="center"
        justify="center"
        style={{
          width: 24,
          height: 24,
          backgroundColor: 'var(--gray-a3)',
          borderRadius: 'var(--radius-1)',
          flexShrink: 0,
        }}
      >
        <MaterialIcon name="open_in_new" size={14} color="var(--gray-11)" />
      </Flex>
    </Flex>
  );
}
