'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Button, Flex, Grid, Heading, Text } from '@radix-ui/themes';
import { toast } from '@/lib/store/toast-store';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import {
  useFeatureFlagsStore,
  selectApiToolsetsEnabled,
  selectFeatureFlagsLoaded,
} from '@/lib/store/feature-flags-store';
import { ConfirmationDialog, WorkspaceHeaderIconButton } from '../components';
import { ApiToolsetsApi } from './api';
import { useApiToolsetsStore } from './store';
import { ApiToolsetCard, ApiToolsetConnectDialog, ApiToolsetEditorPanel } from './components';

export default function ApiToolsetsPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const enabled = useFeatureFlagsStore(selectApiToolsetsEnabled);
  const flagsLoaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  const isAdmin = useUserStore(selectIsAdmin) === true;
  const store = useApiToolsetsStore();
  const [isDeleting, setIsDeleting] = useState(false);

  // Only the latest of overlapping loads applies.
  const loadRequestIdRef = useRef(0);
  const loadData = useCallback(async () => {
    const requestId = ++loadRequestIdRef.current;
    const s = useApiToolsetsStore.getState();
    s.setLoading(true);
    try {
      const instances = await ApiToolsetsApi.listInstances();
      if (requestId === loadRequestIdRef.current) s.setInstances(instances);
    } catch {
      if (requestId === loadRequestIdRef.current) toast.error(t('workspace.apiToolsets.toasts.loadError'));
    } finally {
      if (requestId === loadRequestIdRef.current) s.setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (flagsLoaded && !enabled) router.replace('/workspace/general');
  }, [flagsLoaded, enabled, router]);

  useEffect(() => {
    if (!flagsLoaded || !enabled) return;
    void loadData();
    return () => useApiToolsetsStore.getState().reset();
  }, [flagsLoaded, enabled, loadData]);

  const handleConfirmDelete = useCallback(async () => {
    const target = store.deleteTarget;
    if (!target) return;
    setIsDeleting(true);
    try {
      await ApiToolsetsApi.deleteInstance(target.id);
      toast.success(t('workspace.apiToolsets.toasts.deleted'));
      store.setDeleteTarget(null);
      void loadData();
    } catch (e) {
      toast.error(getUserFacingErrorMessage(e, t('workspace.apiToolsets.toasts.deleteError')));
    } finally {
      setIsDeleting(false);
    }
  }, [store, loadData, t]);

  if (!flagsLoaded || !enabled) return null;

  return (
    <ServiceGate services={['connector']}>
      <Flex
        direction="column"
        gap="5"
        px={{ initial: '4', md: '8' }}
        py="8"
        style={{
          width: '100%',
          height: '100%',
          overflowY: 'auto',
          background: 'linear-gradient(to bottom, var(--olive-2), var(--olive-1))',
        }}
      >
        <Flex justify="between" align="start" gap="3" wrap="wrap">
          <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 240 }}>
            <Heading size="5" weight="medium">
              {t('workspace.apiToolsets.title')}
            </Heading>
            <Text size="2" style={{ color: 'var(--gray-11)' }}>
              {t(isAdmin ? 'workspace.apiToolsets.subtitleAdmin' : 'workspace.apiToolsets.subtitle')}
            </Text>
          </Flex>
          <Flex align="center" gap="2">
            {isAdmin ? (
              <Button size="2" onClick={store.openCreate}>
                <MaterialIcon name="add" size={16} />
                {t('workspace.apiToolsets.cta.add')}
              </Button>
            ) : null}
            <WorkspaceHeaderIconButton icon="refresh" onClick={() => void loadData()} aria-label={t('action.refresh')} />
          </Flex>
        </Flex>

        {store.isLoading && store.instances.length === 0 ? (
          <Flex align="center" justify="center" style={{ flex: 1 }}>
            <LottieLoader variant="loader" size={48} showLabel label={t('workspace.apiToolsets.loading')} />
          </Flex>
        ) : store.instances.length === 0 ? (
          <Flex direction="column" align="center" gap="2" pt="9">
            <MaterialIcon name="api" size={48} color="var(--gray-9)" />
            <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }}>
              {t('workspace.apiToolsets.emptyTitle')}
            </Text>
            <Text size="2" style={{ color: 'var(--gray-10)', textAlign: 'center' }}>
              {t(isAdmin ? 'workspace.apiToolsets.emptyAdmin' : 'workspace.apiToolsets.emptyMember')}
            </Text>
          </Flex>
        ) : (
          <Grid columns={{ initial: '1', sm: '2', lg: '3' }} gap="4">
            {store.instances.map((toolset) => (
              <ApiToolsetCard
                key={toolset.id}
                toolset={toolset}
                isAdmin={isAdmin}
                onConnect={() => store.setConnectTarget(toolset)}
                onEdit={() => store.openEdit(toolset)}
                onDelete={() => store.setDeleteTarget(toolset)}
              />
            ))}
          </Grid>
        )}
      </Flex>

      {isAdmin ? (
        <ApiToolsetEditorPanel
          open={store.editor.open}
          editingId={store.editor.editingId}
          onOpenChange={(open) => {
            if (!open) store.closeEditor();
          }}
          onSaved={() => void loadData()}
        />
      ) : null}

      {store.connectTarget ? (
        <ApiToolsetConnectDialog
          toolset={store.connectTarget}
          isAdmin={isAdmin}
          onOpenChange={(open) => {
            if (!open) store.setConnectTarget(null);
          }}
          onChanged={() => void loadData()}
        />
      ) : null}

      <ConfirmationDialog
        open={store.deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) store.setDeleteTarget(null);
        }}
        title={t('workspace.apiToolsets.deleteDialog.title')}
        message={t('workspace.apiToolsets.deleteDialog.body', { name: store.deleteTarget?.name ?? '' })}
        confirmLabel={t('workspace.apiToolsets.cta.delete')}
        confirmVariant="danger"
        isLoading={isDeleting}
        confirmLoadingLabel={t('action.deleting')}
        onConfirm={() => void handleConfirmDelete()}
      />
    </ServiceGate>
  );
}
