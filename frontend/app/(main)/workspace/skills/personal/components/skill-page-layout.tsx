'use client';

import { lower } from '@/lib/utils/safe';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Callout, Flex, Grid, Heading, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { WorkspaceHeaderIconButton } from '../../../components';
import type { SkillMetadata } from '../types';
import { SkillCard } from './skill-card';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { pagePaddingX } from '@/app/components/ui/responsive';
import { MOBILE_PAGE_PADDING_TOP, PAGE_PADDING_TOP } from '@/app/(main)/workspace/components/settings-page-container';
import { HeaderOverflowMenu } from '@/app/(main)/workspace/components/header-overflow-menu';
import { WorkspaceDocsButton } from '@/app/(main)/workspace/components/workspace-docs-button';

// ========================================
// Props
// ========================================

interface SkillPageLayoutProps {
  skills: SkillMetadata[];
  isLoading: boolean;
  /** Set when the list request failed; shown with a retry instead of the empty state. */
  error?: string | null;
  pendingCandidateCount: number;
  onCreateSkill: () => void;
  onImportSkill: () => void;
  onOpenCandidates: () => void;
  onRefresh: () => void;
  onManage: (name: string) => void;
  onToggleAvailability?: (skill: SkillMetadata, nextEnabled: boolean) => void;
}

// ========================================
// Component
// ========================================

export function SkillPageLayout({
  skills,
  isLoading,
  error = null,
  pendingCandidateCount,
  onCreateSkill,
  onImportSkill,
  onOpenCandidates,
  onRefresh,
  onManage,
  onToggleAvailability,
}: SkillPageLayoutProps) {
  const isMobile = useIsMobile();
  const { t } = useTranslation();
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter(
      (s) =>
        lower(s.name).includes(q) ||
        lower(s.description).includes(q) ||
        lower(s.category).includes(q)
    );
  }, [skills, query]);

  return (
    <Flex
      direction="column"
      gap="5"
      style={{
        width: '100%',
        height: '100%',
        paddingTop: isMobile ? MOBILE_PAGE_PADDING_TOP : PAGE_PADDING_TOP,
        paddingBottom: 64,
        paddingInline: pagePaddingX(100),
        overflowY: 'auto',
        background: 'linear-gradient(to bottom, var(--olive-2), var(--olive-1))',
      }}
    >
      {/* ── Header ── */}
      <Flex justify="between" align="start" wrap="wrap" gap="3" style={{ width: '100%' }}>
        <Flex direction="column" gap="2" style={{ flex: '1 1 240px', minWidth: 0 }}>
          <Flex align="center" gap="2">
            <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.skills.title')}
            </Heading>
            <BetaBadge />
          </Flex>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.skills.subtitle')}
          </Text>
        </Flex>

        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          {isMobile ? (
            <>
              <CreateSkillButton onClick={onCreateSkill} />
              <HeaderOverflowMenu
                items={[
                  ...(pendingCandidateCount > 0
                    ? [{ label: t('workspace.skills.reviewCandidates'), icon: 'auto_awesome', onSelect: onOpenCandidates }]
                    : []),
                  { label: t('workspace.skills.importSkill'), icon: 'download', onSelect: onImportSkill },
                  { label: t('action.refresh'), icon: 'refresh', onSelect: onRefresh },
                  {
                    label: t('workspace.actions.documentation'),
                    icon: 'open_in_new',
                    onSelect: () => window.open('https://docs.pipeshub.com/agents/skills', '_blank'),
                  },
                ]}
              />
            </>
          ) : (
            <>
              <CandidatesButton count={pendingCandidateCount} onClick={onOpenCandidates} />
              <ImportButton onClick={onImportSkill} />
              <CreateSkillButton onClick={onCreateSkill} />
              <WorkspaceHeaderIconButton icon="refresh" onClick={onRefresh} aria-label={t('action.refresh')} />
              <WorkspaceDocsButton href="https://docs.pipeshub.com/agents/skills" />
            </>
          )}
        </Flex>
      </Flex>

      {/* ── Search ── */}
      {skills.length > 0 && (
        <Box width={{ initial: '100%', sm: '224px' }} flexShrink="0">
          <TextField.Root
            size="2"
            placeholder={t('workspace.skills.searchPlaceholder')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ width: '100%' }}
          >
            <TextField.Slot>
              <MaterialIcon name="search" size={16} color="var(--gray-9)" />
            </TextField.Slot>
          </TextField.Root>
        </Box>
      )}

      {/* A failed refresh keeps the cards it already had; say so instead of showing stale data silently. */}
      {!isLoading && error && skills.length > 0 && (
        <Callout.Root color="red" variant="surface" size="1" style={{ width: '100%' }}>
          <Callout.Icon>
            <MaterialIcon name="error_outline" size={16} />
          </Callout.Icon>
          <Flex align="center" justify="between" gap="3" wrap="wrap">
            <Callout.Text>{error}</Callout.Text>
            <Button size="1" variant="soft" color="red" onClick={onRefresh}>
              {t('action.tryAgain')}
            </Button>
          </Flex>
        </Callout.Root>
      )}

      {/* ── Content ── */}
      {isLoading ? (
        <Flex align="center" justify="center" style={{ width: '100%', flex: 1 }}>
          <LottieLoader variant="loader" size={48} showLabel label={t('workspace.skills.loading')} />
        </Flex>
      ) : error && skills.length === 0 ? (
        <Flex direction="column" align="center" justify="center" gap="3" style={{ width: '100%', flex: 1, paddingTop: 80 }}>
          <MaterialIcon name="error_outline" size={48} color="var(--gray-9)" />
          <Text size="2" style={{ color: 'var(--red-11)', textAlign: 'center' }}>
            {error}
          </Text>
          <Button size="2" variant="soft" onClick={onRefresh}>
            {t('action.tryAgain')}
          </Button>
        </Flex>
      ) : skills.length === 0 ? (
        <EmptyState onCreate={onCreateSkill} onImport={onImportSkill} />
      ) : filtered.length === 0 ? (
        <Flex align="center" justify="center" style={{ width: '100%', flex: 1 }}>
          <Text size="2" style={{ color: 'var(--gray-10)' }}>{t('workspace.skills.noResults')}</Text>
        </Flex>
      ) : (
        <Grid columns={{ initial: '1', sm: '2', md: '3', lg: '4' }} gap="4" style={{ width: '100%' }}>
          {filtered.map((skill) => (
            <SkillCard
              key={skill.name}
              skill={skill}
              onManage={() => onManage(skill.name)}
              onToggleAvailability={onToggleAvailability}
            />
          ))}
        </Grid>
      )}
    </Flex>
  );
}

// ========================================
// Sub-components
// ========================================

function PillButton({
  icon,
  label,
  onClick,
  badge,
}: {
  icon: string;
  label: string;
  onClick: () => void;
  badge?: number;
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
        border: '1px solid var(--gray-a4)',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        height: 'var(--space-6)',
        padding: '0 12px',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--gray-a3)' : 'transparent',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
        position: 'relative',
      }}
    >
      <MaterialIcon name={icon} size={16} color="var(--slate-11)" />
      <span style={{ fontSize: 14, fontWeight: 500, color: 'var(--slate-11)' }}>{label}</span>
      {!!badge && (
        <span
          style={{
            marginLeft: 2,
            minWidth: 18,
            height: 18,
            borderRadius: 9,
            padding: '0 5px',
            backgroundColor: 'var(--amber-9)',
            color: 'white',
            fontSize: 11,
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {badge}
        </span>
      )}
    </button>
  );
}

function CandidatesButton({ count, onClick }: { count: number; onClick: () => void }) {
  const { t } = useTranslation();
  if (count === 0) return null;
  return <PillButton icon="auto_awesome" label={t('workspace.skills.reviewCandidates')} onClick={onClick} badge={count} />;
}

function ImportButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return <PillButton icon="download" label={t('workspace.skills.importSkill')} onClick={onClick} />;
}

function CreateSkillButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
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
        gap: 6,
        height: 'var(--space-6)',
        padding: '0 12px',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--accent-10)' : 'var(--accent-9)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon name="add" size={16} color="white" />
      <span style={{ fontSize: 14, fontWeight: 500, color: 'white' }}>{t('workspace.skills.createSkill')}</span>
    </button>
  );
}

function EmptyState({ onCreate, onImport }: { onCreate: () => void; onImport: () => void }) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" align="center" justify="center" gap="3" style={{ width: '100%', flex: 1, paddingTop: 80 }}>
      <MaterialIcon name="psychology" size={48} color="var(--gray-9)" />
      <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
        {t('workspace.skills.emptyTitle')}
      </Text>
      <Text size="2" style={{ color: 'var(--gray-11)' }}>
        {t('workspace.skills.emptyDescription')}
      </Text>
      <Flex gap="2" style={{ marginTop: 'var(--space-2)' }}>
        <CreateSkillButton onClick={onCreate} />
        <ImportButton onClick={onImport} />
      </Flex>
    </Flex>
  );
}
