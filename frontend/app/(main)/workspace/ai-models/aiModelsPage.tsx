'use client';

import React, { useEffect } from 'react';
import { Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useRouter } from 'next/navigation';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { ProviderGrid, ModelRolesSection } from './components';
import { useAiModelsManager } from './hooks/use-ai-models-manager';

export default function AIModelsPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);
  const isProfileInitialized = useUserStore(selectIsProfileInitialized);
  const manager = useAiModelsManager({
    enabled: isProfileInitialized && isAdmin === true,
    providerErrorKey: 'workspace.aiModels.toastLoadProvidersError',
    modelsErrorKey: 'workspace.aiModels.toastLoadModelsError',
  });

  useEffect(() => {
    if (isProfileInitialized && isAdmin === false) {
      router.replace('/workspace/general');
    }
  }, [isProfileInitialized, isAdmin, router]);

  if (!isProfileInitialized || isAdmin === false) return null;

  return (
    <ServiceGate services={['query']}>
      <ProviderGrid
        headerActions={
          manager.capabilitySection === 'text_generation' ? (
            <Button type="button" variant="outline" color="gray" size="2" onClick={manager.openIdentityPolicy}>
              {t('workspace.aiModels.identityPolicy.open')}
            </Button>
          ) : null
        }
        belowHeader={
          manager.capabilitySection === 'text_generation' ? (
            <ModelRolesSection
              configuredModels={manager.configuredModels}
              onRolesUpdated={() => {
                void manager.loadProviders();
                void manager.loadModels();
              }}
            />
          ) : null
        }
        providers={manager.providers}
        configuredModels={manager.configuredModels}
        searchQuery={manager.searchQuery}
        onSearchChange={manager.setSearchQuery}
        mainSection={manager.mainSection}
        onMainSectionChange={manager.setMainSection}
        capabilitySection={manager.capabilitySection}
        onCapabilitySectionChange={manager.setCapabilitySection}
        onAdd={manager.openAdd}
        onEdit={manager.openEdit}
        onSetDefault={manager.handleSetDefault}
        onDelete={manager.openDelete}
        onRotate={manager.openRotate}
        onIdentityForwarding={manager.openIdentityForwarding}
        isLoading={manager.isLoading}
        onRefresh={() => {
          void manager.loadProviders();
          void manager.loadModels();
        }}
      />
      {manager.dialogs}
    </ServiceGate>
  );
}
