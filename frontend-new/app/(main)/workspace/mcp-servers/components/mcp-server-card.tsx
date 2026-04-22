'use client';

import { useState } from 'react';
import { Flex, Text, Badge } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { MCPServerInstance } from '../types';

// ============================================================================
// Props
// ============================================================================

interface McpServerCardProps {
  server: MCPServerInstance;
  onManage: (server: MCPServerInstance) => void;
}

// ============================================================================
// McpServerCard
// ============================================================================

export function McpServerCard({ server, onManage }: McpServerCardProps) {
  const [isHovered, setIsHovered] = useState(false);

  const isAuthenticated = server.isAuthenticated ?? false;
  const toolCount = server.toolCount ?? server.tools?.length ?? 0;

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
      }}
    >
      {/* ── Top section: icon + text ── */}
      <Flex direction="column" gap="3" style={{ width: '100%', flex: 1 }}>
        {/* Icon */}
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
            {server.iconPath ? (
              <img
                src={server.iconPath}
                alt={server.displayName}
                style={{ width: 20, height: 20, objectFit: 'contain' }}
                onError={(e) => {
                  (e.currentTarget as HTMLImageElement).style.display = 'none';
                }}
              />
            ) : (
              <MaterialIcon name="dns" size={20} color="var(--gray-10)" />
            )}
          </Flex>

          {/* Auth status badge */}
          {isAuthenticated ? (
            <Badge color="green" variant="soft" size="1">
              Authenticated
            </Badge>
          ) : (
            <Badge color="orange" variant="soft" size="1">
              Not authenticated
            </Badge>
          )}
        </Flex>

        {/* Title + description */}
        <Flex direction="column" gap="1" style={{ width: '100%' }}>
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {server.instanceName || server.displayName}
          </Text>
          {server.description && (
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
              {server.description}
            </Text>
          )}
        </Flex>

        {/* Tool count */}
        {toolCount > 0 && (
          <Flex align="center" gap="1">
            <MaterialIcon name="build" size={12} color="var(--gray-9)" />
            <Text size="1" style={{ color: 'var(--gray-10)' }}>
              {toolCount} {toolCount === 1 ? 'tool' : 'tools'}
            </Text>
          </Flex>
        )}
      </Flex>

      {/* ── Bottom action ── */}
      <ManageButton onClick={() => onManage(server)} />
    </Flex>
  );
}

// ============================================================================
// Sub-component
// ============================================================================

function ManageButton({ onClick }: { onClick: () => void }) {
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
      <MaterialIcon name="settings" size={16} color="var(--gray-11)" />
      <span
        style={{
          fontSize: 14,
          fontWeight: 500,
          lineHeight: '20px',
          color: 'var(--gray-11)',
        }}
      >
        Manage
      </span>
    </button>
  );
}
