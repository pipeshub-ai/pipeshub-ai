'use client';

import { useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import { useBotsStore } from './store';
import { BotsApi } from './api';
import { BotPageLayout, BotConfigPanel } from './components';
import { useRouter } from 'next/navigation';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { ServiceGate } from '@/app/components/ui/service-gate';


// ========================================
// Page
// ========================================

export default function BotsPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  // A profile that failed to load leaves isAdmin null; General is open to
  // everyone, so that goes there too (as Prompts does).
  useEffect(() => {
    if (isProfileInitialized && isAdmin !== true) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  const {
    slackBotConfigs,
    agents,
    agentsComplete,
    isLoading,
    openPanel,
    setEditingBot,
    setConfigs,
    setAgents,
    setLoading,
    setError,
  } = useBotsStore();

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [configs, agents] = await Promise.allSettled([
        BotsApi.getSlackBotConfigs(),
        BotsApi.getAgents(),
      ]);

      if (configs.status === 'fulfilled') {
        setConfigs(configs.value);
      }
      if (agents.status === 'fulfilled') {
        setAgents(agents.value.agents, agents.value.complete);
      } else {
        setAgents(useBotsStore.getState().agents, false);
      }
      if (configs.status === 'rejected') {
        setError(t('workspace.bots.errors.loadConfigs'));
      }
    } catch {
      setError(t('workspace.bots.errors.loadData'));
    } finally {
      setLoading(false);
    }
  }, [setConfigs, setAgents, setLoading, setError]);

  // Only an admin may read these; a member would get a 403 before the redirect.
  useEffect(() => {
    if (!isProfileInitialized || isAdmin !== true) return;
    fetchData();
  }, [isProfileInitialized, isAdmin, fetchData]);

  const handleRefresh = useCallback(() => {
    fetchData();
    toast.success(t('workspace.bots.refreshed'));
  }, [fetchData]);

  if (!isProfileInitialized || isAdmin !== true) {
    return null;
  }

  return (
    <ServiceGate services={['query']}>
      <BotPageLayout
        configs={slackBotConfigs}
        agents={agents}
        agentsComplete={agentsComplete}
        isLoading={isLoading}
        onCreateBot={openPanel}
        onRefresh={handleRefresh}
        onManage={(configId) => setEditingBot(configId)}
      />
      <BotConfigPanel />
    </ServiceGate>
  );
}
