'use client';

import { useEffect, useRef, useState } from 'react';
import { Box, Button, Dialog, Flex, Progress, Text, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { formatFileSize } from '@/app/components/file-preview/utils';
import { AIModelsApi } from '../api';
import type { DownloadProgressPayload, EmbeddingDownloadStatus } from '../types';

interface EmbeddingDownloadProgressProps {
  open: boolean;
  modelName: string;
  trustRemoteCode: boolean;
  onReady: () => void;
  onCancel: () => void;
}

function statusLabelKey(status: EmbeddingDownloadStatus): string {
  switch (status) {
    case 'checking':
      return 'workspace.aiModels.downloadStatusChecking';
    case 'downloading':
      return 'workspace.aiModels.downloadStatusDownloading';
    case 'loading':
      return 'workspace.aiModels.downloadStatusLoading';
    case 'ready':
      return 'workspace.aiModels.downloadStatusReady';
    case 'failed':
      return 'workspace.aiModels.downloadStatusFailed';
    default:
      return 'workspace.aiModels.downloadStatusChecking';
  }
}

/**
 * Progress dialog for local embedding model downloads. On slim images the
 * model isn't pre-baked, so the first health-check-and-save would otherwise
 * download several hundred MB to several GB behind an opaque spinner and
 * usually lose the race against the 90s axios timeout. This streams the
 * embedding server's real download progress instead.
 */
export function EmbeddingDownloadProgress({
  open,
  modelName,
  trustRemoteCode,
  onReady,
  onCancel,
}: EmbeddingDownloadProgressProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<EmbeddingDownloadStatus>('checking');
  const [progress, setProgress] = useState(0);
  const [downloadedBytes, setDownloadedBytes] = useState(0);
  const [totalBytes, setTotalBytes] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [retryToken, setRetryToken] = useState(0);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  useEffect(() => {
    if (!open) return undefined;

    const controller = new AbortController();
    let settled = false;

    setStatus('checking');
    setProgress(0);
    setDownloadedBytes(0);
    setTotalBytes(0);
    setError(null);

    const run = async () => {
      try {
        await AIModelsApi.prepareModel(modelName, trustRemoteCode);
      } catch (err) {
        if (controller.signal.aborted) return;
        settled = true;
        setStatus('failed');
        setError(
          err instanceof Error ? err.message : t('workspace.aiModels.downloadPrepareError')
        );
        return;
      }

      await AIModelsApi.streamDownloadProgress(modelName, {
        signal: controller.signal,
        onEvent: (event) => {
          const data = event.data as DownloadProgressPayload;
          setStatus(data.status);
          setProgress(data.progress ?? 0);
          setDownloadedBytes(data.downloaded_bytes ?? 0);
          setTotalBytes(data.total_bytes ?? 0);

          if (data.status === 'ready') {
            settled = true;
            onReadyRef.current();
          } else if (data.status === 'failed') {
            settled = true;
            setError(data.error || t('workspace.aiModels.downloadErrorFallback'));
          }
        },
        onError: (err) => {
          if (controller.signal.aborted || settled) return;
          setStatus('failed');
          setError(err.message || t('workspace.aiModels.downloadErrorFallback'));
        },
      });
    };

    void run();

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, modelName, trustRemoteCode, retryToken]);

  const isFailed = status === 'failed';
  const isTerminal = isFailed; // 'ready' closes the dialog via onReady before render.

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && !isFailed && onCancel()}>
      <Dialog.Content
        style={{ maxWidth: '28rem', width: '100%' }}
        onEscapeKeyDown={(e) => !isFailed && e.preventDefault()}
        onInteractOutside={(e) => !isFailed && e.preventDefault()}
      >
        <VisuallyHidden>
          <Dialog.Title>{t('workspace.aiModels.downloadDialogTitle')}</Dialog.Title>
        </VisuallyHidden>
        <Flex direction="column" gap="4">
          <Flex align="center" gap="3">
            <MaterialIcon
              name={isFailed ? 'error' : 'downloading'}
              size={24}
              color={isFailed ? 'var(--red-10)' : 'var(--jade-10)'}
            />
            <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
              <Text size="4" weight="bold">
                {t('workspace.aiModels.downloadDialogTitle')}
              </Text>
              <Text size="2" style={{ color: 'var(--gray-11)', wordBreak: 'break-word' }}>
                {t('workspace.aiModels.downloadDialogDescription', { modelName })}
              </Text>
            </Flex>
          </Flex>

          {!isFailed && (
            <Box>
              <Progress value={progress} max={100} size="2" color="jade" />
              <Flex justify="between" style={{ marginTop: 8 }}>
                <Text size="1" style={{ color: 'var(--gray-11)' }}>
                  {t(statusLabelKey(status))}
                </Text>
                <Text size="1" style={{ color: 'var(--gray-11)' }}>
                  {totalBytes > 0
                    ? t('workspace.aiModels.downloadProgressBytes', {
                        downloaded: formatFileSize(downloadedBytes),
                        total: formatFileSize(totalBytes),
                      })
                    : `${Math.round(progress)}%`}
                </Text>
              </Flex>
            </Box>
          )}

          {isFailed && (
            <Text size="2" style={{ color: 'var(--red-11)' }}>
              {error || t('workspace.aiModels.downloadErrorFallback')}
            </Text>
          )}

          <Flex gap="2" justify="end">
            {isTerminal ? (
              <>
                <Button variant="outline" color="gray" onClick={onCancel}>
                  {t('workspace.aiModels.downloadClose')}
                </Button>
                <Button color="jade" onClick={() => setRetryToken((n) => n + 1)}>
                  {t('workspace.aiModels.downloadRetry')}
                </Button>
              </>
            ) : (
              <Text size="1" style={{ color: 'var(--gray-10)' }}>
                {t('workspace.aiModels.downloadDialogHint')}
              </Text>
            )}
          </Flex>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
