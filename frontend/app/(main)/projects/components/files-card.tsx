'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { formatFileSize } from '@/app/components/file-preview/utils';
import { getIndexStatusIcon } from '@/lib/utils/index-status-icon';
import { KnowledgeHubApi, KnowledgeBaseApi } from '@/knowledge-base/api';
import type { KnowledgeHubNode } from '@/knowledge-base/types';
import { ProjectApi } from '@/chat/project-api';
import { toast } from '@/lib/store/toast-store';
import { useKbStreamUpload } from '../hooks/use-kb-stream-upload';

const FILES_LIST_LIMIT = 100;

const INDEX_STATUS_COLOR: Record<string, string> = {
  COMPLETED: 'var(--emerald-11)',
  IN_PROGRESS: 'var(--amber-9)',
  QUEUED: 'var(--blue-9)',
  FAILED: 'var(--red-9)',
  FILE_TYPE_NOT_SUPPORTED: 'var(--red-9)',
};

interface FilesCardProps {
  projectId: string;
  linkedKnowledgeBaseId: string | null | undefined;
  canEdit: boolean;
  /** Called once the first upload lazily creates the project's hidden KB, so the parent's `project` state stays in sync. */
  onKbCreated: (kbId: string) => void;
}

/**
 * Project Files card — same per-file status experience as Collections
 * uploads (`KnowledgeBaseApi.streamUpload` + the global `UploadProgressTracker`),
 * backed by the project's lazily-created hidden Collection
 * (`linkedKnowledgeBaseId`, see `ProjectKnowledgeBaseService.ensureLinkedKb`).
 */
export function FilesCard({ projectId, linkedKnowledgeBaseId, canEdit, onKbCreated }: FilesCardProps) {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<KnowledgeHubNode[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const load = useCallback(async (kbId: string) => {
    setIsLoading(true);
    setLoadError(false);
    try {
      const res = await KnowledgeHubApi.getNodeChildren('kb', kbId, {
        onlyContainers: false,
        limit: FILES_LIST_LIMIT,
      });
      setItems(res.items ?? []);
    } catch {
      setLoadError(true);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (linkedKnowledgeBaseId) {
      void load(linkedKnowledgeBaseId);
    } else {
      setItems([]);
    }
  }, [linkedKnowledgeBaseId, load]);

  const getKbId = useCallback(async (): Promise<string> => {
    if (linkedKnowledgeBaseId) return linkedKnowledgeBaseId;
    const kbId = await ProjectApi.ensureKnowledgeBase(projectId);
    onKbCreated(kbId);
    return kbId;
  }, [linkedKnowledgeBaseId, projectId, onKbCreated]);

  const { uploadFiles } = useKbStreamUpload({ getKbId });

  const handleFilesSelected = useCallback(
    (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return;
      void uploadFiles(Array.from(fileList))
        .then(() =>
          // Re-resolve the kbId (idempotent — `ensureLinkedKb` short-circuits
          // once linked) since the very first upload creates it mid-flight and
          // this closure's `linkedKnowledgeBaseId` may still be stale.
          getKbId().then((kbId) => load(kbId)),
        )
        .catch(() => setLoadError(true));
    },
    [uploadFiles, getKbId, load],
  );

  const handleRemove = useCallback(
    async (recordId: string) => {
      if (!linkedKnowledgeBaseId) return;
      setDeletingId(recordId);
      try {
        await KnowledgeBaseApi.deleteRecord(recordId);
        setItems((prev) => prev.filter((i) => i.id !== recordId));
      } catch {
        toast.error(t('chat.projects.workspace.removeFile'));
      } finally {
        setDeletingId(null);
      }
    },
    [linkedKnowledgeBaseId, t],
  );

  return (
    <Flex direction="column" gap="2">
      <Flex align="center" justify="between">
        <Text size="1" style={{ color: 'var(--slate-10)' }}>
          {t('chat.projects.workspace.filesSubtitle', {
            defaultValue: 'Files here are searched only within this project.',
          })}
        </Text>
        {canEdit && (
          <>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                handleFilesSelected(e.target.files);
                e.target.value = '';
              }}
            />
            <LoadingButton size="1" variant="ghost" color="gray" onClick={() => fileInputRef.current?.click()}>
              {t('chat.projects.workspace.uploadFiles')}
            </LoadingButton>
          </>
        )}
      </Flex>

      {loadError ? (
        <Text size="2" style={{ color: '#ef4444' }}>
          {t('chat.projects.workspace.failedToLoad')}
        </Text>
      ) : isLoading ? (
        <Text size="2" style={{ color: 'var(--slate-10)' }}>
          {t('common.loading', { defaultValue: 'Loading…' })}
        </Text>
      ) : items.length === 0 ? (
        <Text size="2" style={{ color: 'var(--slate-10)' }}>
          {t('chat.projects.workspace.noFiles')}
        </Text>
      ) : (
        <Flex direction="column" gap="1">
          {items.map((item) => (
            <Flex
              key={item.id}
              align="center"
              justify="between"
              gap="2"
              style={{
                padding: 'var(--space-2)',
                borderRadius: 'var(--radius-2)',
                background: 'var(--olive-1)',
              }}
            >
              <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
                <FileIcon extension={item.extension ?? undefined} mimeType={item.mimeType ?? undefined} size={16} />
                <Text
                  size="1"
                  style={{
                    color: 'var(--slate-12)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {item.name}
                </Text>
                {item.indexingStatus && (
                  <MaterialIcon
                    name={getIndexStatusIcon(item.indexingStatus)}
                    size={14}
                    color={INDEX_STATUS_COLOR[item.indexingStatus] ?? 'var(--slate-10)'}
                  />
                )}
                {typeof item.sizeInBytes === 'number' && (
                  <Text size="1" style={{ color: 'var(--slate-10)', flexShrink: 0 }}>
                    {formatFileSize(item.sizeInBytes)}
                  </Text>
                )}
              </Flex>
              {canEdit && (
                <button
                  type="button"
                  aria-label={t('chat.projects.workspace.removeFile')}
                  disabled={deletingId === item.id}
                  onClick={() => void handleRemove(item.id)}
                  style={{
                    appearance: 'none',
                    border: 'none',
                    background: 'transparent',
                    cursor: deletingId === item.id ? 'default' : 'pointer',
                    display: 'flex',
                    flexShrink: 0,
                    opacity: deletingId === item.id ? 0.5 : 1,
                  }}
                >
                  <MaterialIcon name="close" size={14} color="var(--slate-10)" />
                </button>
              )}
            </Flex>
          ))}
        </Flex>
      )}
    </Flex>
  );
}
