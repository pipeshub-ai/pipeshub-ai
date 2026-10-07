'use client';

import { useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useFeatureFlagGuard } from '@/lib/hooks/use-feature-flag-guard';
import { selectSkillsEnabled } from '@/lib/store/feature-flags-store';
import { useSkillsStore } from './store';
import { SkillsApi } from './api';
import type { SkillMetadata } from './types';
import {
  SkillPageLayout,
  SkillEditorPanel,
  SkillImportDialog,
  SkillCandidatesPanel,
} from './components';

// ========================================
// Page
// ========================================

export default function PersonalSkillsPage() {
  const { t } = useTranslation();
  const { ready } = useFeatureFlagGuard(selectSkillsEnabled);
  const {
    skills,
    candidates,
    isLoading,
    setSkills,
    updateSkillMetadata,
    setCandidates,
    setLoading,
    setError,
    openCreateEditor,
    openEditEditor,
    openImportDialog,
    openCandidatesPanel,
  } = useSkillsStore();

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [skillsResult, candidatesResult] = await Promise.allSettled([
        SkillsApi.listSkills(),
        SkillsApi.getPendingCandidates(),
      ]);

      if (skillsResult.status === 'fulfilled') {
        setSkills(skillsResult.value);
      } else {
        setError(t('workspace.skills.errors.loadSkills'));
      }
      if (candidatesResult.status === 'fulfilled') {
        setCandidates(candidatesResult.value);
      }
    } catch {
      setError(t('workspace.skills.errors.loadData'));
    } finally {
      setLoading(false);
    }
  }, [setSkills, setCandidates, setLoading, setError, t]);

  useEffect(() => {
    // Avoids a 403 toast if the flag was just disabled and this page is
    // mid-redirect (`useFeatureFlagGuard` handles the navigate-away).
    if (!ready) return;
    fetchData();
  }, [ready, fetchData]);

  const handleRefresh = useCallback(() => {
    fetchData();
    toast.success(t('workspace.skills.refreshed'));
  }, [fetchData, t]);

  const handleToggleAvailability = useCallback(
    async (skill: SkillMetadata, nextEnabled: boolean) => {
      const previousStatus = skill.status;
      const optimisticStatus = nextEnabled ? 'active' : 'disabled';
      updateSkillMetadata(skill.name, { status: optimisticStatus });
      try {
        const updated = nextEnabled
          ? await SkillsApi.enableSkill(skill.name)
          : await SkillsApi.disableSkill(skill.name);
        updateSkillMetadata(skill.name, updated);
        toast.success(
          nextEnabled
            ? t('workspace.skills.toasts.enabled', { name: skill.name })
            : t('workspace.skills.toasts.disabled', { name: skill.name })
        );
      } catch {
        updateSkillMetadata(skill.name, { status: previousStatus });
        toast.error(t('workspace.skills.toasts.toggleError'));
      }
    },
    [updateSkillMetadata, t]
  );

  return (
    <ServiceGate services={['query']}>
      <SkillPageLayout
        skills={skills}
        isLoading={isLoading}
        pendingCandidateCount={candidates.length}
        onCreateSkill={openCreateEditor}
        onImportSkill={openImportDialog}
        onOpenCandidates={openCandidatesPanel}
        onRefresh={handleRefresh}
        onManage={openEditEditor}
        onToggleAvailability={handleToggleAvailability}
      />
      <SkillEditorPanel />
      <SkillImportDialog />
      <SkillCandidatesPanel />
    </ServiceGate>
  );
}
