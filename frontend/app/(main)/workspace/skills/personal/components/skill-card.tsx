'use client';

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { SkillMetadata } from '../types';

// ========================================
// Status -> badge color
// ========================================

const STATUS_COLOR: Record<string, 'green' | 'gray' | 'amber'> = {
  active: 'green',
  deprecated: 'gray',
  candidate: 'amber',
};

const SOURCE_ICON: Record<string, string> = {
  builtin: 'verified',
  manual: 'edit_note',
  imported: 'download',
  learned: 'auto_awesome',
};

// ========================================
// Props
// ========================================

interface SkillCardProps {
  skill: SkillMetadata;
  onManage: () => void;
}

// ========================================
// SkillCard
// ========================================

export function SkillCard({ skill, onManage }: SkillCardProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);
  const isBuiltin = skill.source === 'builtin';

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
        padding: 'var(--space-3)',
        gap: 'var(--space-6)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <Flex direction="column" gap="3" style={{ width: '100%', flex: 1 }}>
        <Flex align="center" justify="between" style={{ width: '100%' }}>
          <Flex
            align="center"
            justify="center"
            style={{
              width: 'var(--space-8)',
              height: 'var(--space-8)',
              padding: 'var(--space-2)',
              backgroundColor: 'var(--gray-a2)',
              borderRadius: 'var(--radius-1)',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name={SOURCE_ICON[skill.source] ?? 'psychology'} size={16} color="var(--gray-10)" />
          </Flex>
          <Flex gap="1">
            {skill.status !== 'active' && (
              <Badge color={STATUS_COLOR[skill.status] ?? 'gray'} size="1">
                {t(`workspace.skills.status.${skill.status}`)}
              </Badge>
            )}
          </Flex>
        </Flex>

        <Flex direction="column" gap="1" style={{ width: '100%' }}>
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {skill.name}
          </Text>
          <Text
            size="1"
            style={{
              color: 'var(--gray-11)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              minHeight: 32,
            }}
          >
            {skill.description}
          </Text>
          <Flex align="center" gap="2" style={{ marginTop: 4 }}>
            <Text size="1" style={{ color: 'var(--gray-9)' }}>v{skill.version}</Text>
            {skill.category && (
              <Text size="1" style={{ color: 'var(--gray-9)' }}>· {skill.category}</Text>
            )}
          </Flex>
        </Flex>
      </Flex>

      <ManageButton onClick={onManage} readOnly={isBuiltin} />
    </Flex>
  );
}

// ========================================
// Sub-components
// ========================================

function ManageButton({ onClick, readOnly }: { onClick: () => void; readOnly: boolean }) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        border: '1px solid var(--accent-a6)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 'var(--space-2)',
        width: '100%',
        height: 'var(--space-6)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--accent-a4)' : 'var(--accent-a3)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon name={readOnly ? 'visibility' : 'settings'} size={16} color="var(--accent-11)" />
      <span style={{ fontSize: 14, fontWeight: 500, lineHeight: '20px', color: 'var(--accent-11)' }}>
        {readOnly ? t('workspace.skills.cta.view') : t('workspace.skills.cta.manage')}
      </span>
    </button>
  );
}
