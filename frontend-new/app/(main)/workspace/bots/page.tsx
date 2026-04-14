'use client';

import { useEffect, useCallback } from 'react';
import { toast } from '@/lib/store/toast-store';
import { useBotsStore } from './store';
import { BotsApi } from './api';
import { BotPageLayout, BotConfigPanel } from './components';
import { useRouter } from 'next/navigation';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';


// ========================================
// Page
// ========================================

export default function BotsPage() {
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

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
        setError('Failed to load bot configurations');
      }
    } catch {
      setError('Failed to load data');
    } finally {
      setLoading(false);
    }
  }, [setConfigs, setAgents, setLoading, setError]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleRefresh = useCallback(() => {
    fetchData();
    toast.success('Refreshed');
  }, [fetchData]);

  return (
    <>
      <BotPageLayout
        configs={slackBotConfigs}
        agents={agents}
        isLoading={isLoading}
        onCreateBot={openPanel}
        onRefresh={handleRefresh}
        onManage={(configId) => setEditingBot(configId)}
      />
      <BotConfigPanel />
    </>
  );
}
