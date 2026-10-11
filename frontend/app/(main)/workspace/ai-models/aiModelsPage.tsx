'use client';

import React, { useEffect } from 'react';
import { Box } from '@radix-ui/themes';
import { useRouter } from 'next/navigation';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useUserStore, selectIsAdmin, selectIsProfileInitialized } from '@/lib/store/user-store';
import { ProviderGrid, ModelRolesSection } from './components';
import { useAiModelsManager } from './hooks/use-ai-models-manager';

export default function AIModelsPage() {
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

  const pagePaddingX = 'clamp(var(--space-4), 4vw, 100px)';
  const pagePaddingY = 'clamp(var(--space-6), 3vw, 64px)';

  return (
    <ServiceGate services={['query']}>
      {manager.capabilitySection === 'text_generation' && (
        <Box
          style={{
            paddingTop: pagePaddingY,
            paddingLeft: pagePaddingX,
            paddingRight: pagePaddingX,
            paddingBottom: 0,
          }}
        >
          <ModelRolesSection
            configuredModels={manager.configuredModels}
            onRolesUpdated={() => {
              void manager.loadProviders();
              void manager.loadModels();
            }}
          />
        </Box>
      )}

      <ProviderGrid
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
