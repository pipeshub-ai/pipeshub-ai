'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { SkillForBuilder } from '../../types';
import type { NodeTemplate } from '../types';
import { prepareDragData } from '../sidebar-utils';
import { AgentBuilderPaletteSkeletonList } from './agent-builder-palette-skeleton';

const PALETTE_ICON_SIZE = 20;

const paletteNestStyle: React.CSSProperties = {
  minWidth: 0,
  marginLeft: 10,
  paddingLeft: 14,
  gap: 6,
  borderLeft: '1px solid var(--olive-4)',
};

const rowLabelStyle: React.CSSProperties = {
  flex: 1,
  minWidth: 0,
  fontSize: 15,
  fontWeight: 500,
  lineHeight: '22px',
  color: 'var(--olive-12)',
  whiteSpace: 'normal',
  overflowWrap: 'anywhere',
  wordBreak: 'break-word',
  textAlign: 'left',
};

function SkillPaletteRow(props: {
  template: NodeTemplate;
  isBuiltin: boolean;
  structureLocked: boolean;
  onPaletteStructureDragBlocked?: () => void;
}) {
  const { template, isBuiltin, structureLocked, onPaletteStructureDragBlocked } = props;
  const [hovered, setHovered] = useState(false);
  const showHover = hovered && !structureLocked;

  return (
    <Box
      draggable={!structureLocked}
      onDragStart={(e) => {
        if (structureLocked) {
          e.preventDefault();
          onPaletteStructureDragBlocked?.();
          return;
        }
        e.dataTransfer.effectAllowed = 'move';
        Object.entries(prepareDragData(template)).forEach(([k, v]) => {
          if (v != null) e.dataTransfer.setData(k, v);
        });
      }}
      onMouseEnter={() => {
        if (!structureLocked) setHovered(true);
      }}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex',
        alignItems: 'center',
        width: '100%',
        minWidth: 0,
        minHeight: 36,
        padding: '0 12px',
        boxSizing: 'border-box',
        gap: 8,
        cursor: structureLocked ? 'not-allowed' : 'grab',
        opacity: structureLocked ? 0.55 : 1,
        borderRadius: 'var(--radius-1)',
        border: `1px solid ${showHover ? 'var(--olive-4)' : 'transparent'}`,
        backgroundColor: showHover ? 'var(--olive-3)' : 'transparent',
        boxShadow: 'none',
        transition: 'background-color 0.12s ease, border-color 0.12s ease, box-shadow 0.12s ease',
      }}
    >
      <MaterialIcon
        name={isBuiltin ? 'verified' : 'psychology'}
        size={PALETTE_ICON_SIZE}
        color="var(--slate-11)"
        style={{ flexShrink: 0 }}
      />
      <span style={rowLabelStyle}>{template.label}</span>
    </Box>
  );
}

/**
 * Flat draggable list of `skill-*` node templates (no per-skill nesting,
 * unlike Tools/MCP which group by app/server) — matches the plan's "flat
 * draggable list" call for the Skills palette section.
 */
export function AgentBuilderSkillsSection(props: {
  availableSkills: SkillForBuilder[];
  skillTemplates: NodeTemplate[];
  loading: boolean;
  structureLocked?: boolean;
  onPaletteStructureDragBlocked?: () => void;
}) {
  const { availableSkills, skillTemplates, loading, structureLocked = false, onPaletteStructureDragBlocked } = props;
  const { t } = useTranslation();

  if (loading) {
    return <AgentBuilderPaletteSkeletonList count={3} />;
  }

  if (skillTemplates.length === 0) {
    return (
      <Text size="1" style={{ color: 'var(--slate-11)', fontStyle: 'italic', padding: '4px 8px' }}>
        {t('agentBuilder.noSkillsAvailable')}
      </Text>
    );
  }

  const isBuiltinByName = new Map(availableSkills.map((s) => [s.name, s.isBuiltin]));

  return (
    <Flex direction="column" style={paletteNestStyle}>
      {skillTemplates.map((template) => {
        const skillName = String(template.defaultConfig?.skillName ?? '');
        return (
          <SkillPaletteRow
            key={template.type}
            template={template}
            isBuiltin={isBuiltinByName.get(skillName) ?? false}
            structureLocked={structureLocked}
            onPaletteStructureDragBlocked={onPaletteStructureDragBlocked}
          />
        );
      })}
    </Flex>
  );
}
