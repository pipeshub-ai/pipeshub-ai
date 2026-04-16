'use client';

import React, { useState, useCallback } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { getConnectorIconPath } from '@/lib/utils/connector-icon-utils';
import type { DocumentationLink } from '../../types';

// ========================================
// DocumentationSection
// ========================================

export function DocumentationSection({
  links,
  connectorIconPath,
}: {
  links: DocumentationLink[];
  connectorIconPath: string;
}) {
  const iconSrc = getConnectorIconPath(connectorIconPath);

  return (
    <Flex direction="column" gap="3">
      <Flex direction="column" gap="1">
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          Setup Documentation
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          Follow the guide to set up your connector
        </Text>
      </Flex>

      <Flex direction="column" gap="2">
        {links.map((link, idx) => (
          <DocumentationLinkRow
            key={idx}
            link={link}
            iconSrc={iconSrc}
          />
        ))}
      </Flex>
    </Flex>
  );
}

// ========================================
// DocumentationLinkRow
// ========================================

function DocumentationLinkRow({
  link,
  iconSrc,
}: {
  link: DocumentationLink;
  iconSrc: string;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const [iconError, setIconError] = useState(false);

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
        {iconError ? (
          <MaterialIcon name="hub" size={16} color="var(--gray-9)" />
        ) : (
          <img
            src={iconSrc}
            alt=""
            width={16}
            height={16}
            onError={() => setIconError(true)}
            style={{ display: 'block', objectFit: 'contain' }}
          />
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
