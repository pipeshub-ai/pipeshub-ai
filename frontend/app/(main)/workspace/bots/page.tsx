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
  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin]);

  const {
    slackBotConfigs,
    agents,
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
        setAgents(agents.value);
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

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleRefresh = useCallback(() => {
    fetchData();
    toast.success(t('workspace.bots.refreshed'));
  }, [fetchData]);

  return (
    <ServiceGate services={['query']}>
      <BotPageLayout
        configs={slackBotConfigs}
        agents={agents}
        isLoading={isLoading}
        onCreateBot={openPanel}
        onRefresh={handleRefresh}
        onManage={(configId) => setEditingBot(configId)}
      />
      <BotConfigPanel />
    </ServiceGate>
  );
}
