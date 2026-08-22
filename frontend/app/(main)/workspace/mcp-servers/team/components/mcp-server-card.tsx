'use client';

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// ========================================
// Types
// ========================================

interface McpServerCardProps {
  title: string;
  description: string;
  iconUrl?: string | null;
  /** 'registry' shows "+ Setup". 'active' shows an instance-count badge + "+". */
  variant: 'registry' | 'active';
  instanceCount?: number;
  /** Fired when "+ Setup" (registry) is clicked. */
  onSetup?: () => void;
  /** Fired when "+" (active — add another instance of this type) is clicked. */
  onAddInstance?: () => void;
  /** Fired when the badge / card body (active) is clicked to manage existing instance(s). */
  onManage?: () => void;
}

// ========================================
// Component
// ========================================

export function McpServerCard({
  title,
  description,
  iconUrl,
  variant,
  instanceCount = 0,
  onSetup,
  onAddInstance,
  onManage,
}: McpServerCardProps) {
  const [isHovered, setIsHovered] = useState(false);

  const isManageable = variant === 'active';

  return (
    <Flex
      direction="column"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={() => {
        if (isManageable) onManage?.();
      }}
      role={isManageable ? 'button' : undefined}
      tabIndex={isManageable ? 0 : undefined}
      onKeyDown={
        isManageable
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onManage?.();
              }
            }
          : undefined
      }
      style={{
        width: '100%',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 12,
        gap: 24,
        cursor: variant === 'active' ? 'pointer' : 'default',
        transition: 'background-color 150ms ease',
      }}
    >
      <Flex direction="column" gap="3" style={{ width: '100%', flex: 1 }}>
        <Flex
          align="center"
          justify="center"
          style={{
            width: 32,
            height: 32,
            padding: 8,
            backgroundColor: 'var(--gray-a2)',
            borderRadius: 'var(--radius-1)',
            flexShrink: 0,
            overflow: 'hidden',
          }}
        >
          {iconUrl ? (
            <img src={iconUrl} alt="" width={16} height={16} />
          ) : (
            <MaterialIcon name="hub" size={16} color="var(--gray-10)" />
          )}
        </Flex>

        <Flex direction="column" gap="1" style={{ width: '100%' }}>
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {title}
          </Text>
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
            {description}
          </Text>
        </Flex>
      </Flex>

      {variant === 'registry' ? (
        <SetupButton onClick={onSetup} />
      ) : (
        <InstanceCountBar
          count={instanceCount}
          onAdd={onAddInstance}
          onBadgeClick={onManage}
        />
      )}
    </Flex>
  );
}

// ========================================
// Sub-components
// ========================================

function SetupButton({ onClick }: { onClick?: () => void }) {
  const [isHovered, setIsHovered] = useState(false);
  const { t } = useTranslation();

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick?.();
      }}
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
      <MaterialIcon name="add" size={16} color="var(--gray-11)" />
      <span style={{ fontSize: 14, fontWeight: 500, lineHeight: '20px', color: 'var(--gray-11)' }}>
        {t('workspace.actions.cta.setup')}
      </span>
    </button>
  );
}

/** Instance-count badge + "+" add button, mirrors connectors' `ActiveInstanceBar` (single count only). */
function InstanceCountBar({
  count,
  onAdd,
  onBadgeClick,
}: {
  count: number;
  onAdd?: () => void;
  onBadgeClick?: () => void;
}) {
  const [isAddHovered, setIsAddHovered] = useState(false);
  const { t } = useTranslation();

  return (
    <Flex align="center" gap="2" style={{ width: '100%' }}>
      <Flex
        align="center"
        justify="center"
        onClick={(e) => {
          e.stopPropagation();
          onBadgeClick?.();
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            e.stopPropagation();
            onBadgeClick?.();
          }
        }}
        style={{
          flex: 1,
          height: 28,
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--green-a3)',
          padding: '0 8px',
          cursor: 'pointer',
        }}
      >
        <Text size="1" weight="medium" style={{ color: 'var(--green-a11)', whiteSpace: 'nowrap' }}>
          {count === 1
            ? t('workspace.actions.card.activeOne')
            : t('workspace.actions.card.activeMany', { count })}
        </Text>
      </Flex>

      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onAdd?.();
        }}
        onMouseEnter={() => setIsAddHovered(true)}
        onMouseLeave={() => setIsAddHovered(false)}
        style={{
          appearance: 'none',
          margin: 0,
          padding: 0,
          border: 'none',
          outline: 'none',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: 32,
          height: 32,
          borderRadius: 'var(--radius-2)',
          backgroundColor: isAddHovered ? 'var(--gray-a4)' : 'var(--gray-a3)',
          cursor: 'pointer',
          flexShrink: 0,
          transition: 'background-color 150ms ease',
        }}
      >
        <MaterialIcon name="add" size={16} color="var(--gray-11)" />
      </button>
    </Flex>
  );
}
