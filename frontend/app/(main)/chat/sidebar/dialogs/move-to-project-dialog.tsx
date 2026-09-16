'use client';

import { useEffect, useState } from 'react';
import { Box, Dialog, Flex, RadioGroup, Text, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { Spinner } from '@/app/components/ui/spinner';
import { ProjectApi } from '@/chat/project-api';
import { useChatStore } from '@/chat/store';

export interface MoveToProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Current project this conversation is linked to, if any. */
  currentProjectId?: string | null;
  onConfirm: (projectId: string | null) => Promise<void>;
}

/**
 * Lets the user move a conversation into a project (or back out to "No
 * project"). Reuses `useChatStore.projects` — the same list the sidebar's
 * `ProjectsSection` shows — fetching it lazily the first time this opens.
 */
export function MoveToProjectDialog({
  open,
  onOpenChange,
  currentProjectId,
  onConfirm,
}: MoveToProjectDialogProps) {
  const { t } = useTranslation();
  const projects = useChatStore((s) => s.projects);
  const setProjects = useChatStore((s) => s.setProjects);
  const [isLoading, setIsLoading] = useState(false);
  const [isMoving, setIsMoving] = useState(false);
  const [selected, setSelected] = useState<string | null>(currentProjectId ?? null);

  useEffect(() => {
    if (!open) return;
    setSelected(currentProjectId ?? null);
    if (projects.length > 0) return;
    setIsLoading(true);
    ProjectApi.list({ scope: 'all', limit: 100, includeArchived: false })
      .then((res) => setProjects(res.projects))
      .catch(() => setProjects([]))
      .finally(() => setIsLoading(false));
  }, [open, currentProjectId]);

  const handleConfirm = async () => {
    if (isMoving) return;
    setIsMoving(true);
    try {
      await onConfirm(selected);
      onOpenChange(false);
    } finally {
      setIsMoving(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !isMoving && onOpenChange(v)}>
      {open && (
        <Box style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(28, 32, 36, 0.5)', zIndex: 999 }} />
      )}
      <Dialog.Content style={{ maxWidth: '26rem', width: '100%', padding: 'var(--space-5)', zIndex: 1000 }}>
        <VisuallyHidden>
          <Dialog.Title>{t('chat.projects.moveDialog.title')}</Dialog.Title>
        </VisuallyHidden>
        <Flex direction="column" gap="4">
          <Text size="4" weight="bold" style={{ color: 'var(--olive-12)' }}>
            {t('chat.projects.moveDialog.title')}
          </Text>

          {isLoading ? (
            <Flex align="center" justify="center" style={{ padding: 'var(--space-4)' }}>
              <Spinner size={20} />
            </Flex>
          ) : (
            <RadioGroup.Root value={selected ?? '__none__'} onValueChange={(v) => setSelected(v === '__none__' ? null : v)}>
              <Flex direction="column" gap="2" style={{ maxHeight: 280, overflowY: 'auto' }}>
                <Flex asChild align="center" gap="2" style={{ padding: 'var(--space-2)', cursor: 'pointer' }}>
                  <label>
                    <RadioGroup.Item value="__none__" />
                    <Text size="2">{t('chat.projects.moveDialog.removeOption')}</Text>
                  </label>
                </Flex>
                {projects.length === 0 ? (
                  <Text size="2" style={{ color: 'var(--slate-10)', padding: 'var(--space-2)' }}>
                    {t('chat.projects.moveDialog.noProjects')}
                  </Text>
                ) : (
                  projects.map((p) => (
                    <Flex
                      asChild
                      key={p._id}
                      align="center"
                      gap="2"
                      style={{ padding: 'var(--space-2)', cursor: 'pointer' }}
                    >
                      <label>
                        <RadioGroup.Item value={p._id} />
                        <MaterialIcon name="folder" size={16} color="var(--slate-11)" />
                        <Text size="2" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {p.name}
                        </Text>
                      </label>
                    </Flex>
                  ))
                )}
              </Flex>
            </RadioGroup.Root>
          )}

          <Flex gap="2" justify="end">
            <LoadingButton
              type="button"
              variant="soft"
              color="gray"
              size="2"
              onClick={() => onOpenChange(false)}
              disabled={isMoving}
            >
              {t('action.cancel')}
            </LoadingButton>
            <LoadingButton
              type="button"
              size="2"
              color="jade"
              onClick={() => void handleConfirm()}
              loading={isMoving}
              loadingLabel={t('chat.projects.moveDialog.moving')}
              disabled={selected === (currentProjectId ?? null)}
            >
              {t('action.save')}
            </LoadingButton>
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
