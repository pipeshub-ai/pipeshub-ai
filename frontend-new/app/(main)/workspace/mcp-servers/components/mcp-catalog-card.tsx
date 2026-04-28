'use client';

import { useState } from 'react';
import Image from 'next/image';
import { Flex, Text, Badge } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { MCPServerTemplate } from '../types';

// ============================================================================
// Props
// ============================================================================

interface McpCatalogCardProps {
  template: MCPServerTemplate;
  /** Whether this template already has at least one configured instance */
  isConfigured?: boolean;
  onAdd: (template: MCPServerTemplate) => void;
}

// ============================================================================
// McpCatalogCard
// ============================================================================

export function McpCatalogCard({
  template,
  isConfigured = false,
  onAdd,
}: McpCatalogCardProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [iconError, setIconError] = useState(false);

  return (
    <Flex
      direction="column"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        width: '100%',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 12,
        gap: 24,
        transition: 'background-color 150ms ease',
        opacity: isConfigured ? 0.75 : 1,
      }}
    >
      {/* ── Top section: icon + text ── */}
      <Flex direction="column" gap="3" style={{ width: '100%', flex: 1 }}>
        {/* Icon + configured badge */}
        <Flex align="center" justify="between">
          <Flex
            align="center"
            justify="center"
            style={{
              width: 32,
              height: 32,
              padding: 6,
              backgroundColor: 'var(--gray-a2)',
              borderRadius: 'var(--radius-1)',
              flexShrink: 0,
            }}
          >
            {template.iconPath && !iconError ? (
              <Image
                src={template.iconPath}
                alt={template.displayName}
                width={20}
                height={20}
                unoptimized
                onError={() => setIconError(true)}
                style={{ objectFit: 'contain' }}
              />
            ) : (
              <MaterialIcon name="dns" size={20} color="var(--gray-10)" />
            )}
          </Flex>

          {isConfigured && (
            <Badge color="jade" variant="soft" size="1">
              Added
            </Badge>
          )}
        </Flex>

        {/* Title + description */}
        <Flex direction="column" gap="1" style={{ width: '100%' }}>
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {template.displayName}
          </Text>
          {template.description && (
            <Text
              size="2"
              style={{
                color: 'var(--gray-11)',
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
            >
              {template.description}
            </Text>
          )}
        </Flex>

        {/* Tags */}
        {template.tags.length > 0 && (
          <Flex wrap="wrap" gap="1">
            {template.tags.slice(0, 3).map((tag) => (
              <Badge key={tag} color="gray" variant="surface" size="1">
                {tag}
              </Badge>
            ))}
          </Flex>
        )}
      </Flex>

      {/* ── Bottom action ── */}
      <AddButton isConfigured={isConfigured} onClick={() => onAdd(template)} />
    </Flex>
  );
}

// ============================================================================
// Sub-component
// ============================================================================

function AddButton({
  isConfigured,
  onClick,
}: {
  isConfigured: boolean;
  onClick: () => void;
}) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        border: 'none',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        width: '100%',
        height: 32,
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--gray-a4)' : 'var(--gray-a3)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon
        name={isConfigured ? 'add' : 'add'}
        size={16}
        color="var(--gray-11)"
      />
      <span
        style={{
          fontSize: 14,
          fontWeight: 500,
          lineHeight: '20px',
          color: 'var(--gray-11)',
        }}
      >
        {isConfigured ? 'Add Another' : 'Add'}
      </span>
    </button>
  );
}
