'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge, Button, Flex, Grid, Text } from '@radix-ui/themes';
import { toast } from '@/lib/store/toast-store';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { useFeatureFlagsStore, selectApiToolsetsEnabled } from '@/lib/store/feature-flags-store';
import { ConfirmationDialog } from '../../components';
import { ApiToolsetsApi } from '../api';
import { useApiToolsetsStore } from '../store';
import { matchesSearch, type ApiActionFilter } from '../toolset-form';
import { ApiActionCard } from './api-action-card';
import { ApiActionConnectionPanel } from './api-action-connection-panel';
import { ApiToolsetEditorPanel } from './api-toolset-editor-panel';

interface ApiActionsSectionProps {
  /** The team page: admins add, edit and delete here, and set shared credentials. */
  manage: boolean;
  searchQuery: string;
  filter: ApiActionFilter;
}

/** The org's API actions inside an Actions catalog page, above the app actions. */
export function ApiActionsSection({ manage, searchQuery, filter }: ApiActionsSectionProps) {
  const { t } = useTranslation();
  const enabled = useFeatureFlagsStore(selectApiToolsetsEnabled);
  const isAdmin = useUserStore(selectIsAdmin) === true;
  const canManage = manage && isAdmin;
  const store = useApiToolsetsStore();
  const [loaded, setLoaded] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  // Only the latest of overlapping loads applies.
  const loadRequestIdRef = useRef(0);
  const load = useCallback(async () => {
    const requestId = ++loadRequestIdRef.current;
    try {
      const instances = await ApiToolsetsApi.listInstances();
      if (requestId === loadRequestIdRef.current) useApiToolsetsStore.getState().setInstances(instances);
    } catch {
      if (requestId === loadRequestIdRef.current) toast.error(t('workspace.apiToolsets.toasts.loadError'));
    } finally {
      if (requestId === loadRequestIdRef.current) setLoaded(true);
    }
  }, [t]);

  useEffect(() => {
    if (!enabled) return;
    void load();
    return () => useApiToolsetsStore.getState().reset();
  }, [enabled, load]);

  const visible = useMemo(
    () => store.instances.filter((i) => matchesSearch(i, searchQuery) && filter(i)),
    [store.instances, searchQuery, filter],
  );

  const handleConfirmDelete = useCallback(async () => {
    const target = store.deleteTarget;
    if (!target) return;
    setIsDeleting(true);
    try {
      await ApiToolsetsApi.deleteInstance(target.id);
      toast.success(t('workspace.apiToolsets.toasts.deleted'));
      store.setDeleteTarget(null);
      void load();
    } catch (e) {
      toast.error(getUserFacingErrorMessage(e, t('workspace.apiToolsets.toasts.deleteError')));
    } finally {
      setIsDeleting(false);
    }
  }, [store, load, t]);

  // Members only see the section once there is something in it; admins always see where to add one.
  const filtering = Boolean(searchQuery.trim());
  if (!enabled || !loaded || (visible.length === 0 && (!canManage || filtering || store.instances.length > 0))) {
    return null;
  }

  return (
    <Flex direction="column" gap="3" style={{ width: '100%' }}>
      <Flex align="end" justify="between" gap="3" wrap="wrap">
        <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 240 }}>
          <Flex align="center" gap="2">
            <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.apiToolsets.sectionTitle')}
            </Text>
            <Badge size="1" variant="soft" color="gray" radius="full">
              {visible.length}
            </Badge>
            <BetaBadge />
          </Flex>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t(canManage ? 'workspace.apiToolsets.subtitleAdmin' : 'workspace.apiToolsets.subtitle')}
          </Text>
        </Flex>
        {canManage ? (
          <Button size="2" variant="soft" onClick={store.openCreate} style={{ cursor: 'pointer' }}>
            <MaterialIcon name="add" size={16} />
            {t('workspace.apiToolsets.cta.add')}
          </Button>
        ) : null}
      </Flex>

      <Grid columns={{ initial: '1', sm: '2', md: '3' }} gap="4" style={{ width: '100%' }}>
        {visible.map((toolset) => (
          <ApiActionCard
            key={toolset.id}
            toolset={toolset}
            isAdmin={canManage}
            onOpen={() => store.setConnectTarget(toolset)}
            onEdit={canManage ? () => store.openEdit(toolset) : undefined}
            onDelete={canManage ? () => store.setDeleteTarget(toolset) : undefined}
          />
        ))}
        {visible.length === 0 ? <AddApiActionCard onAdd={store.openCreate} /> : null}
      </Grid>

      {canManage ? (
        <ApiToolsetEditorPanel
          open={store.editor.open}
          editingId={store.editor.editingId}
          onOpenChange={(open) => {
            if (!open) store.closeEditor();
          }}
          onSaved={() => void load()}
        />
      ) : null}

      {store.connectTarget ? (
        <ApiActionConnectionPanel
          toolset={store.connectTarget}
          isAdmin={canManage}
          onOpenChange={(open) => {
            if (!open) store.setConnectTarget(null);
          }}
          onChanged={() => void load()}
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
    </Flex>
  );
}

/** The admin's first step when the org has no API actions yet. */
function AddApiActionCard({ onAdd }: { onAdd: () => void }) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);
  return (
    <button
      type="button"
      onClick={onAdd}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        textAlign: 'left',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        padding: 16,
        minHeight: 160,
        justifyContent: 'center',
        border: '1px dashed var(--accent-7)',
        borderRadius: 'var(--radius-1)',
        background: isHovered ? 'var(--accent-a3)' : 'var(--accent-a2)',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <MaterialIcon name="add_link" size={24} color="var(--accent-11)" />
      <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
        {t('workspace.apiToolsets.emptyTitle')}
      </Text>
      <Text size="2" style={{ color: 'var(--gray-11)' }}>
        {t('workspace.apiToolsets.emptyAdmin')}
      </Text>
    </button>
  );
}
