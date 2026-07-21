'use client';

import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Button, Flex, ScrollArea, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { WorkspaceRightPanel } from '../../../components';
import { toast } from '@/lib/store/toast-store';
import { useSkillsStore } from '../store';
import { SkillsApi } from '../api';
import type { SkillCandidate } from '../types';

// ========================================
// Component
// ========================================

/**
 * Governance review queue for `SkillManager`'s learning loop — candidates the
 * agent runtime proposed while operating (see `SkillGovernor`) surface here
 * for a human approve/reject before they become real, listable skills.
 */
export function SkillCandidatesPanel() {
  const { t } = useTranslation();
  const {
    candidatesPanelOpen,
    closeCandidatesPanel,
    candidates,
    setCandidates,
    setSkills,
    openEditorPanel,
  } = useSkillsStore();
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setCandidates(await SkillsApi.getPendingCandidates());
    } catch {
      toast.error(t('workspace.skills.toasts.loadError'));
    } finally {
      setLoading(false);
    }
  }, [setCandidates, t]);

  useEffect(() => {
    if (candidatesPanelOpen) refresh();
  }, [candidatesPanelOpen, refresh]);

  const handleApprove = useCallback(async (candidate: SkillCandidate) => {
    setBusyId(candidate.candidate_id);
    try {
      await SkillsApi.approveCandidate(candidate.candidate_id);
      toast.success(t('workspace.skills.toasts.candidateApproved', { name: candidate.name }));
      await refresh();
      setSkills(await SkillsApi.listSkills());
    } catch {
      toast.error(t('workspace.skills.toasts.candidateActionError'));
    } finally {
      setBusyId(null);
    }
  }, [refresh, setSkills, t]);

  const handleReject = useCallback(async (candidate: SkillCandidate) => {
    setBusyId(candidate.candidate_id);
    try {
      await SkillsApi.rejectCandidate(candidate.candidate_id);
      toast.success(t('workspace.skills.toasts.candidateRejected', { name: candidate.name }));
      await refresh();
    } catch {
      toast.error(t('workspace.skills.toasts.candidateActionError'));
    } finally {
      setBusyId(null);
    }
  }, [refresh, t]);

  const handleEditAndApprove = useCallback((candidate: SkillCandidate) => {
    closeCandidatesPanel();
    openEditorPanel(null, {
      prefillName: candidate.name,
      prefillDescription: candidate.description ?? '',
      prefillBody: candidate.body ?? '',
      prefillCategory: candidate.category ?? undefined,
      prefillSubcategory: candidate.subcategory ?? undefined,
      prefillTags: candidate.tags ?? [],
      candidateId: candidate.candidate_id,
    });
  }, [closeCandidatesPanel, openEditorPanel]);

  const toggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  return (
    <WorkspaceRightPanel
      open={candidatesPanelOpen}
      onOpenChange={(open) => { if (!open) closeCandidatesPanel(); }}
      title={t('workspace.skills.candidates.title')}
      icon={<MaterialIcon name="auto_awesome" size={20} color="var(--slate-12)" />}
      hideFooter
    >
      {loading ? (
        <Text size="2" style={{ color: 'var(--gray-10)' }}>{t('workspace.skills.loading')}</Text>
      ) : candidates.length === 0 ? (
        <Flex direction="column" align="center" justify="center" gap="2" style={{ paddingTop: 60 }}>
          <MaterialIcon name="check_circle" size={32} color="var(--gray-8)" />
          <Text size="2" style={{ color: 'var(--gray-10)' }}>{t('workspace.skills.candidates.empty')}</Text>
        </Flex>
      ) : (
        <Flex direction="column" gap="3">
          <Text size="1" style={{ color: 'var(--gray-10)' }}>{t('workspace.skills.candidates.subtitle')}</Text>
          {candidates.map((candidate) => {
            const isExpanded = expandedId === candidate.candidate_id;
            return (
              <Flex
                key={candidate.candidate_id}
                direction="column"
                gap="2"
                style={{
                  padding: 'var(--space-3)',
                  border: '1px solid var(--olive-3)',
                  borderRadius: 'var(--radius-2)',
                  background: isExpanded ? 'var(--olive-2)' : undefined,
                }}
              >
                <Flex align="center" gap="2">
                  <MaterialIcon name="auto_awesome" size={16} color="var(--amber-9)" />
                  <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', flex: 1 }}>{candidate.name}</Text>
                  {candidate.category && (
                    <Badge size="1" variant="soft" color="gray">{candidate.category}</Badge>
                  )}
                </Flex>
                {candidate.description && (
                  <Text size="1" style={{ color: 'var(--gray-11)' }}>{candidate.description}</Text>
                )}

                {/* Expanded body view */}
                {isExpanded && candidate.body && (
                  <ScrollArea
                    style={{
                      maxHeight: 240,
                      border: '1px solid var(--olive-4)',
                      borderRadius: 'var(--radius-1)',
                      padding: 'var(--space-2)',
                      background: 'var(--olive-1)',
                      marginTop: 4,
                    }}
                  >
                    <Text
                      as="div"
                      size="1"
                      style={{
                        fontFamily: 'var(--font-mono, monospace)',
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        color: 'var(--slate-11)',
                      }}
                    >
                      {candidate.body}
                    </Text>
                  </ScrollArea>
                )}

                {/* Source trajectory summary if present */}
                {isExpanded && candidate.source_trajectory_summary && (
                  <Flex direction="column" gap="1" style={{ marginTop: 4 }}>
                    <Text size="1" weight="medium" style={{ color: 'var(--gray-10)' }}>
                      How it was learned:
                    </Text>
                    <Text size="1" style={{ color: 'var(--gray-9)', fontStyle: 'italic' }}>
                      {candidate.source_trajectory_summary}
                    </Text>
                  </Flex>
                )}

                <Flex gap="2" align="center" style={{ marginTop: 4 }}>
                  <Button
                    variant="soft"
                    color="gray"
                    size="1"
                    onClick={() => toggleExpand(candidate.candidate_id)}
                    style={{ cursor: 'pointer' }}
                  >
                    <MaterialIcon
                      name={isExpanded ? 'visibility_off' : 'visibility'}
                      size={14}
                    />
                    {isExpanded ? t('workspace.skills.candidates.hide') : t('workspace.skills.candidates.view')}
                  </Button>
                  <Button
                    variant="soft"
                    size="1"
                    onClick={() => handleEditAndApprove(candidate)}
                    disabled={!!busyId}
                    style={{ cursor: 'pointer' }}
                  >
                    <MaterialIcon name="edit" size={14} />
                    {t('workspace.skills.candidates.editAndApprove')}
                  </Button>
                  <LoadingButton
                    variant="solid"
                    color="green"
                    size="1"
                    onClick={() => handleApprove(candidate)}
                    loading={busyId === candidate.candidate_id}
                    disabled={!!busyId && busyId !== candidate.candidate_id}
                  >
                    {t('workspace.skills.candidates.approve')}
                  </LoadingButton>
                  <Button
                    variant="outline"
                    color="red"
                    size="1"
                    onClick={() => handleReject(candidate)}
                    disabled={!!busyId}
                    style={{ cursor: 'pointer' }}
                  >
                    {t('workspace.skills.candidates.reject')}
                  </Button>
                </Flex>
              </Flex>
            );
          })}
        </Flex>
      )}
    </WorkspaceRightPanel>
  );
}
