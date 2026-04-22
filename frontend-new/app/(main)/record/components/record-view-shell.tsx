'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertDialog, Box, Flex, Text, IconButton, Button, Tooltip } from '@radix-ui/themes';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { FilePreviewRenderer } from '@/app/components/file-preview/renderers/file-preview-renderer';
import { isPresentationFile, isDocxFile, shouldShowPagination } from '@/app/components/file-preview/utils';
import type { PaginationControls } from '@/app/components/file-preview/types';
import { KnowledgeBaseApi } from '@/app/(main)/knowledge-base/api';
import { toast } from '@/lib/store/toast-store';
import type { RecordDetailsResponse } from '@/app/(main)/knowledge-base/types';
import { DeleteConfirmationDialog } from '@/app/(main)/knowledge-base/components/dialogs/delete-confirmation-dialog';
import { RecordMetadataPanel } from './record-metadata-panel';
import { ZoomActionBar } from './zoom-action-bar';
import { LoadingScreen } from '@/app/components/ui/auth-guard';

const ZOOM_MIN = 0.25;
const ZOOM_MAX = 3;
const ZOOM_STEP = 0.1;

interface RecordViewShellProps {
  recordId: string;
}

/** Mirrors the old getWebUrl() from show-documents.tsx */
function resolveWebUrl(record: RecordDetailsResponse['record']): string | null {
  if (record.hideWeburl) return null;
  let url = record.webUrl || record.fileRecord?.webUrl || null;
  if (!url) return null;
  if (record.origin === 'UPLOAD' && !url.startsWith('http')) {
    url = `${window.location.protocol}//${window.location.host}${url}`;
  }
  return url;
}

/** Returns a Material icon name matching the record type. */
function recordTypeIcon(recordType: string): string {
  switch (recordType) {
    case 'EMAIL':
      return 'email';
    case 'TICKET':
      return 'confirmation_number';
    case 'WEBPAGE':
      return 'language';
    case 'MESSAGE':
      return 'chat';
    default:
      return 'description';
  }
}

export function RecordViewShell({ recordId }: RecordViewShellProps) {
  const { t } = useTranslation();
  const router = useRouter();
  const { appearance } = useThemeAppearance();
  const previewHostRef = useRef<HTMLDivElement>(null);

  const [recordDetails, setRecordDetails] = useState<RecordDetailsResponse | null>(null);
  const [fileName, setFileName] = useState('');
  const [fileUrl, setFileUrl] = useState('');
  const [fileBlob, setFileBlob] = useState<Blob | undefined>(undefined);
  const [fileType, setFileType] = useState('');
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState<number | null>(null);
  const [isFs, setIsFs] = useState(false);
  const [zoomLevel, setZoomLevel] = useState(1);

  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const [isForceReindexOpen, setIsForceReindexOpen] = useState(false);
  const [isReindexing, setIsReindexing] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const blobUrlRef = useRef<string | null>(null);

  const revokeBlobUrl = useCallback(() => {
    if (blobUrlRef.current?.startsWith('blob:')) {
      URL.revokeObjectURL(blobUrlRef.current);
    }
    blobUrlRef.current = null;
  }, []);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    setRecordDetails(null);
    revokeBlobUrl();
    setFileUrl('');
    setFileBlob(undefined);
    setFileName('');
    setFileType('');
    setCurrentPage(1);
    setTotalPages(null);

    (async () => {
      try {
        const details = await KnowledgeBaseApi.getRecordDetails(recordId);
        if (cancelled) return;

        const canPreviewRecord =
          details.record.recordType === 'FILE' &&
          details.record.previewRenderable !== false;

        const name = details.record.recordName || details.record.fileRecord?.name || 'Record';
        const resolvedType = details.record.mimeType || '';

        if (!canPreviewRecord) {
          setRecordDetails(details);
          setFileName(name);
          setFileType(resolvedType);
          setIsLoading(false);
          return;
        }

        const streamOptions = isPresentationFile(
          details.record.mimeType,
          details.record.recordName,
        )
          ? { convertTo: 'application/pdf' as const }
          : undefined;

        const blob = await KnowledgeBaseApi.streamRecord(recordId, streamOptions);

        if (cancelled) return;

        const isDocx = isDocxFile(resolvedType, name);
        const nextUrl = isDocx ? '' : URL.createObjectURL(blob);

        if (!isDocx) {
          blobUrlRef.current = nextUrl;
        }

        setRecordDetails(details);
        setFileName(name);
        setFileType(resolvedType);
        setFileBlob(isDocx ? blob : undefined);
        setFileUrl(isDocx ? '' : nextUrl);
        setIsLoading(false);
      } catch (e) {
        if (cancelled) return;
        const message = e instanceof Error ? e.message : t('recordView.loadFailed');
        setError(message);
        setIsLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [recordId, revokeBlobUrl, t]);

  useEffect(() => () => revokeBlobUrl(), [revokeBlobUrl]);

  useEffect(() => {
    const onFs = () => setIsFs(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', onFs);
    return () => document.removeEventListener('fullscreenchange', onFs);
  }, []);

  const handleTotalPagesDetected = useCallback((n: number) => {
    setTotalPages(n);
  }, []);

  const handlePageChange = useCallback((page: number) => {
    setCurrentPage(page);
  }, []);

  useEffect(() => {
    setCurrentPage(1);
    setTotalPages(null);
    setZoomLevel(1);
  }, [recordId, fileUrl, fileBlob]);

  const handlePrevPage = () => setCurrentPage((p) => Math.max(1, p - 1));
  const handleNextPage = () => {
    if (totalPages !== null) {
      setCurrentPage((p) => Math.min(totalPages, p + 1));
    }
  };

  const handleZoomIn = useCallback(() => {
    setZoomLevel((z) => Math.min(ZOOM_MAX, Math.round((z + ZOOM_STEP) * 100) / 100));
  }, []);

  const handleZoomOut = useCallback(() => {
    setZoomLevel((z) => Math.max(ZOOM_MIN, Math.round((z - ZOOM_STEP) * 100) / 100));
  }, []);

  const handleFitScreen = useCallback(() => {
    setZoomLevel(1);
  }, []);

  const paginationControls: PaginationControls = {
    currentPage,
    totalPages,
    onPageChange: handlePageChange,
    onTotalPagesDetected: handleTotalPagesDetected,
  };

  const paginationVisibility = shouldShowPagination(fileType, fileName, totalPages, isLoading, false);
  const hasError = !isLoading && !!error;
  const headerTitle = recordDetails?.record.recordName || fileName || t('recordView.loading');
  const handleFullscreenToggle = async () => {
    const el = previewHostRef.current;
    if (!el) return;
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await el.requestFullscreen();
      }
    } catch {
      /* ignore */
    }
  };

  const handleDownload = useCallback(() => {
    let url = fileUrl;
    let created = false;
    if (!url && fileBlob) {
      url = URL.createObjectURL(fileBlob);
      created = true;
    }
    if (!url) return;
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    if (created) setTimeout(() => URL.revokeObjectURL(url), 100);
  }, [fileUrl, fileBlob, fileName]);

  const handleReindexClick = () => {
    const indexingStatus = recordDetails?.record?.indexingStatus;
    if (indexingStatus === 'COMPLETED') {
      setIsForceReindexOpen(true);
    } else {
      handleForceReindex();
    }
  };

  const handleForceReindex = async () => {
    setIsReindexing(true);
    setIsForceReindexOpen(false);
    try {
      await toast.promise(KnowledgeBaseApi.reindexItem(recordId), {
        loading: t('recordView.reindexLoading'),
        success: t('recordView.reindexSuccess'),
        error: t('recordView.reindexError'),
      });
      const updated = await KnowledgeBaseApi.getRecordDetails(recordId);
      setRecordDetails(updated);
    } catch {
      // error already shown via toast
    } finally {
      setIsReindexing(false);
    }
  };

  const handleDeleteConfirm = async () => {
    setIsDeleting(true);
    try {
      await KnowledgeBaseApi.deleteRecord(recordId);
      setIsDeleteOpen(false);
      router.push('/');
    } finally {
      setIsDeleting(false);
    }
  };

  const indexingStatus = recordDetails?.record?.indexingStatus;

  const getReindexButtonLabel = (status: string | undefined): string => {
    switch (status) {
      case 'COMPLETED':
        return t('recordView.forceReindex');
      case 'AUTO_INDEX_OFF':
      case 'NOT_STARTED':
        return t('recordView.startIndexing');
      case 'PAUSED':
        return t('recordView.resumeIndexing');
      default:
        return t('recordView.retryIndexing');
    }
  };

  const reindexButtonLabel = getReindexButtonLabel(indexingStatus);
  const isIndexingCompleted = indexingStatus === 'COMPLETED';

  const isConnectorRecord = recordDetails?.record?.origin === 'CONNECTOR';

  const canPreview = recordDetails
    ? recordDetails.record.recordType === 'FILE' &&
    recordDetails.record.previewRenderable !== false
    : true;

  const showZoom = !isLoading && !hasError && canPreview;

  const webUrl = recordDetails ? resolveWebUrl(recordDetails.record) : null;

  const handleOpenExternal = () => {
    if (webUrl) {
      window.open(webUrl, '_blank', 'noopener,noreferrer');
    }
  };



  if (isLoading) {
    return (
      <LoadingScreen />
    );
  }

  return (
    <Flex
      direction="column"
      data-appearance={appearance}
      style={{
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
        width: '100%',
        flex: 1,
        backgroundColor: 'var(--gray-1)',
      }}
    >
      <Flex
        align="center"
        justify="between"
        style={{
          flexShrink: 0,
          padding: 'var(--space-2) var(--space-4)',
          borderBottom: '1px solid var(--olive-4)',
          backgroundColor: 'var(--gray-2)',
          height: '64px',
        }}
      >
        <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
          <FileIcon filename={fileName} mimeType={fileType} size={18} fallbackIcon="description" />
          <Text
            size="2"
            weight="medium"
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              color: 'var(--gray-12)',
            }}
          >
            {headerTitle}
          </Text>
        </Flex>
        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          {!isLoading && !hasError && (
            <>
              <Button
                variant="outline"
                color="gray"
                size="2"
                onClick={handleReindexClick}
                disabled={isReindexing}
                aria-label={reindexButtonLabel}
              >
                <MaterialIcon name="sync" size={ICON_SIZES.HEADER} />
                {reindexButtonLabel}
              </Button>
              {!isConnectorRecord && (
                <Button
                  variant="outline"
                  color="red"
                  size="2"
                  onClick={() => setIsDeleteOpen(true)}
                  aria-label={t('recordView.deleteRecord')}
                >
                  <MaterialIcon name="delete" size={ICON_SIZES.HEADER} />
                  {t('recordView.deleteRecord')}
                </Button>
              )}
            </>
          )}
        </Flex>
      </Flex>

      <Flex align="stretch" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <Box
          ref={previewHostRef}
          style={{
            flex: showZoom ? '1 1 72%' : '1 1 75%',
            minWidth: 0,
            position: 'relative',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <Box
            style={{
              flex: 1,
              minHeight: 0,
              display: 'flex',
              alignItems: 'stretch',
              justifyContent: 'center',
              padding: 'var(--space-6)',
              overflow: 'auto',
              overscrollBehavior: 'contain',
              background: 'linear-gradient(180deg, var(--gray-2) 0%, var(--gray-1) 100%)',
            }}
            className="no-scrollbar"
          >
            {hasError ? (
              <Flex direction="column" align="center" justify="center" gap="3" style={{ padding: 'var(--space-6)' }}>
                <MaterialIcon name="error_outline" size={48} color="var(--red-9)" />
                <Text size="3" weight="medium" color="red">
                  {error}
                </Text>
              </Flex>
            ) : !canPreview ? (
              <Flex
                direction="column"
                align="center"
                justify="center"
                gap="4"
                style={{ width: '100%', height: '100%', minHeight: '280px' }}
              >
                <Box
                  style={{
                    width: 72,
                    height: 72,
                    borderRadius: 'var(--radius-3)',
                    backgroundColor: 'var(--gray-3)',
                    border: '1px solid var(--olive-4)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  {recordDetails?.record.recordType === 'FILE' ? (
                    <FileIcon
                      filename={fileName}
                      mimeType={fileType}
                      size={36}
                      fallbackIcon="description"
                    />
                  ) : (
                    <MaterialIcon
                      name={recordTypeIcon(recordDetails?.record.recordType ?? '')}
                      size={36}
                      color="var(--gray-9)"
                    />
                  )}
                </Box>

                <Flex direction="column" align="center" gap="1" style={{ maxWidth: '360px', textAlign: 'center' }}>
                  <Text
                    size="3"
                    weight="medium"
                    style={{
                      color: 'var(--gray-12)',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                    }}
                  >
                    {fileName}
                  </Text>
                  <Box
                    style={{
                      backgroundColor: 'var(--gray-3)',
                      border: '1px solid var(--olive-4)',
                      borderRadius: 'var(--radius-2)',
                      padding: '2px var(--space-2)',
                    }}
                  >
                    <Text size="1" weight="medium" style={{ color: 'var(--gray-10)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                      {recordDetails?.record.recordType ?? ''}
                    </Text>
                  </Box>
                </Flex>

                {webUrl ? (
                  <Button variant="solid" color="jade" size="3" onClick={handleOpenExternal}>
                    <MaterialIcon name="open_in_new" size={18} />
                    {t('recordView.openExternal')}
                  </Button>
                ) : (
                  <Text size="2" style={{ color: 'var(--gray-9)', textAlign: 'center', maxWidth: '280px' }}>
                    {t('recordView.previewUnavailable')}
                  </Text>
                )}
              </Flex>
            ) : (
              <div
                style={{
                  zoom: zoomLevel,
                  display: 'flex',
                  alignItems: 'stretch',
                  justifyContent: 'center',
                  flex: 1,
                }}
              >
                <FilePreviewRenderer
                  fileUrl={fileUrl}
                  fileName={fileName}
                  fileType={fileType}
                  fileBlob={fileBlob}
                  pagination={paginationControls}
                />
              </div>
            )}
          </Box>

          {showZoom && (
            <Flex
              align="center"
              justify="center"
              gap="3"
              wrap="wrap"
              style={{
                position: 'absolute',
                bottom: 'var(--space-6)',
                left: '50%',
                transform: 'translateX(-50%)',
                padding: 'var(--space-2) var(--space-4)',
                backgroundColor: 'var(--gray-3)',
                border: '1px solid var(--olive-5)',
                borderRadius: 'var(--radius-full)',
                boxShadow: '0px 12px 32px rgba(0, 0, 0, 0.35)',
                maxWidth: 'calc(100% - var(--space-8))',
                zIndex: 10,
              }}
            >
              {paginationVisibility.shouldShow ? (
                <>
                  <Flex align="center" gap="2">
                    <IconButton
                      variant="ghost"
                      color="gray"
                      size="1"
                      onClick={handlePrevPage}
                      disabled={currentPage === 1}
                      style={{ width: 28, height: 28, padding: 0 }}
                      aria-label="Previous page"
                    >
                      <MaterialIcon name="chevron_left" size={ICON_SIZES.SECONDARY} />
                    </IconButton>
                    <Box
                      style={{
                        backgroundColor: 'var(--gray-4)',
                        border: '1px solid var(--olive-5)',
                        borderRadius: 'var(--radius-2)',
                        padding: '4px 12px',
                      }}
                    >
                      <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
                        {totalPages === null ? `${currentPage}/?` : `${currentPage}/${totalPages}`}
                      </Text>
                    </Box>
                    <IconButton
                      variant="ghost"
                      color="gray"
                      size="1"
                      onClick={handleNextPage}
                      disabled={totalPages === null || currentPage === totalPages}
                      style={{ width: 28, height: 28, padding: 0 }}
                      aria-label="Next page"
                    >
                      <MaterialIcon name="chevron_right" size={ICON_SIZES.SECONDARY} />
                    </IconButton>
                  </Flex>
                  <Box style={{ width: 1, alignSelf: 'stretch', backgroundColor: 'var(--olive-5)', flexShrink: 0 }} />
                </>
              ) : null}

              <Tooltip content={t('recordView.download')}>
                <Button variant="outline" color="gray" size="2" onClick={handleDownload}>
                  <MaterialIcon name="download" size={18} />
                </Button>
              </Tooltip>

              <Tooltip content={isFs ? t('recordView.exitFullscreen') : t('recordView.openFullscreen')}>
                <Button variant="outline" color="gray" size="2" onClick={handleFullscreenToggle}>
                  <MaterialIcon name={isFs ? 'close_fullscreen' : 'open_in_full'} size={18} />
                </Button>
              </Tooltip>
            </Flex>
          )}
        </Box>

        {showZoom && (
          <Box
            style={{
              flex: '0 0 3%',
              minWidth: '44px',
              maxWidth: '56px',
              minHeight: 0,
              display: 'flex',
              flexDirection: 'column',
              borderLeft: '1px solid var(--olive-4)',
            }}
          >
            <ZoomActionBar
              zoomLevel={zoomLevel}
              onZoomIn={handleZoomIn}
              onZoomOut={handleZoomOut}
              onFitScreen={handleFitScreen}
              minZoom={ZOOM_MIN}
              maxZoom={ZOOM_MAX}
            />
          </Box>
        )}

        <Box
          style={{
            flex: '0 0 25%',
            minWidth: 'min(320px, 100%)',
            maxWidth: '440px',
            minHeight: 0,
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            borderLeft: '1px solid var(--olive-4)',
          }}
        >
          {recordDetails ? (
            <RecordMetadataPanel recordDetails={recordDetails} />
          ) : hasError ? (
            <Flex align="center" justify="center" style={{ flex: 1, padding: 'var(--space-4)' }}>
              <Text size="2" color="gray" style={{ textAlign: 'center' }}>
                {error}
              </Text>
            </Flex>
          ) : null}
        </Box>
      </Flex>

      {/* Force Reindex Confirmation Dialog */}
      <AlertDialog.Root open={isForceReindexOpen} onOpenChange={setIsForceReindexOpen}>
        <AlertDialog.Content maxWidth="28rem">
          <AlertDialog.Title>
            <Flex align="center" gap="2">
              <MaterialIcon name="sync" size={20} />
              {t('recordView.forceReindexTitle')}
            </Flex>
          </AlertDialog.Title>
          <AlertDialog.Description size="2" style={{ color: 'var(--gray-11)' }}>
            {t('recordView.forceReindexDescription')}
          </AlertDialog.Description>
          <Flex gap="3" justify="end" style={{ marginTop: 'var(--space-4)' }}>
            <AlertDialog.Cancel>
              <Button variant="outline" color="gray">
                {t('action.cancel')}
              </Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action>
              <Button
                variant="solid"
                color="jade"
                onClick={handleForceReindex}
                disabled={isReindexing}
              >
                <MaterialIcon name="sync" size={16} />
                {t('recordView.forceReindex')}
              </Button>
            </AlertDialog.Action>
          </Flex>
        </AlertDialog.Content>
      </AlertDialog.Root>

      {/* Delete Confirmation Dialog */}
      <DeleteConfirmationDialog
        open={isDeleteOpen}
        onOpenChange={setIsDeleteOpen}
        onConfirm={handleDeleteConfirm}
        itemName={fileName}
        itemType="record"
        isDeleting={isDeleting}
      />
    </Flex>
  );
}
