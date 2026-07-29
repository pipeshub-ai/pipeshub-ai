'use client';

import { useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useSkillsStore } from './store';
import { SkillsApi } from './api';
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
  const {
    skills,
    candidates,
    isLoading,
    setSkills,
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
    fetchData();
  }, [fetchData]);

  const handleRefresh = useCallback(() => {
    fetchData();
    toast.success(t('workspace.skills.refreshed'));
  }, [fetchData, t]);

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
      />
      <SkillEditorPanel />
      <SkillImportDialog />
      <SkillCandidatesPanel />
    </ServiceGate>
  );
}
