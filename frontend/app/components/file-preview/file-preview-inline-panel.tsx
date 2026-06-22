'use client';

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { FilePreviewTabs } from './file-preview-tabs';
import { FilePreviewRenderer } from './renderers/file-preview-renderer';
import { FileDetailsTab } from './file-details-tab';
import { CitationsPanel } from './citations-panel';
import { useCitationSync } from './use-citation-sync';
import { usePdfZoom } from './use-pdf-zoom';
import { downloadPreviewFile, getTabsForSource, shouldShowPagination, resolvePreviewIconExtension } from './utils';
import { PDF_ZOOM_MAX, PDF_ZOOM_MIN } from './types';
import type { FilePreviewProps, FilePreviewTab, PaginationControls } from './types';
import { useCitationsColumnResize } from './use-citations-column-resize';

/**
 * Props for FilePreviewInlinePanel.
 *
 * Extends FilePreviewProps but drops Dialog-specific fields (`open`,
 * `onOpenChange`, `onExitFullscreen`). The component renders as a plain flex
 * column that fits any container — no Dialog wrapper, no mobile redirect.
 */
export interface FilePreviewInlinePanelProps
  extends Omit<FilePreviewProps, 'open' | 'onOpenChange' | 'onExitFullscreen'> {
  /** Called when the close (×) button is pressed. */
  onClose?: () => void;
  /**
   * When true, a left-edge resize strip is rendered so the Dialog shell can
   * expand/shrink the panel by dragging. Omit (or false) for split-pane mode
   * where the resize is managed externally by the parent.
   */
  showLeftEdgeResizeHandle?: boolean;
  /** Pointer-down handler for the left-edge resize strip. */
  onPointerDownLeftEdgeResize?: (e: React.PointerEvent) => void;
  /** Optional inline styles applied to the root container. */
  style?: React.CSSProperties;
}

/**
 * The body of the file-preview panel — header, tabs, renderer, citations —
 * without any Dialog wrapper or mobile detection. Used directly in the chat
 * split-pane layout, and also composed inside `FilePreviewSidebar`'s Dialog
 * shell.
 */
export function FilePreviewInlinePanel({
  source,
  file,
  defaultTab = 'preview',
  onClose,
  onToggleFullscreen,
  isLoading = false,
  error,
  recordDetails,
  initialPage,
  highlightBox,
  citations,
  initialCitationId,
  hideFileDetails,
  showDownload,
  showLeftEdgeResizeHandle = false,
  onPointerDownLeftEdgeResize,
  style,
}: FilePreviewInlinePanelProps) {
  const { t } = useTranslation();
  const hasCitations = citations && citations.length > 0;
  const hasError = !isLoading && !!error;
  const canDownload =
    !!showDownload && !isLoading && !hasError && (!!file.blob || !!file.url);

  const { citationsWidthPx, beginCitationsSplitResize } = useCitationsColumnResize();

  const [activeTab, setActiveTab] = useState<FilePreviewTab>(defaultTab);
  const [currentPage, setCurrentPage] = useState(initialPage ?? 1);
  const [totalPages, setTotalPages] = useState<number | null>(null);

  const { pdfScale, setPdfScale, handlePdfZoomIn, handlePdfZoomOut, isZoomLocked, toggleZoomLock } =
    usePdfZoom(file.id, file.url, initialPage);

  const tabs = useMemo(
    () => getTabsForSource(source, { hideFileDetails }),
    [source, hideFileDetails],
  );

  const paginationVisibility = shouldShowPagination(
    file.type,
    file.name,
    totalPages,
    isLoading,
    false,
  );

  // Reset pagination when the file changes
  useEffect(() => {
    setCurrentPage(initialPage ?? 1);
    setTotalPages(null);
  }, [file.id, file.url, initialPage]);

  // If the active tab disappears (e.g. artifacts have no file-details), fall back to preview
  useEffect(() => {
    const stillVisible = tabs.some((t) => t.id === activeTab && t.visible);
    if (!stillVisible) setActiveTab('preview');
  }, [tabs, activeTab]);

  const handleTotalPagesDetected = useCallback((numPages: number) => {
    setTotalPages(numPages);
  }, []);

  const handleTabChange = (tab: FilePreviewTab) => setActiveTab(tab);

  const handlePrevPage = useCallback(() => setCurrentPage((prev) => Math.max(1, prev - 1)), []);

  // Stable ref for totalPages so handleNextPage callback stays stable
  const totalPagesRef = useRef(totalPages);
  useEffect(() => { totalPagesRef.current = totalPages; }, [totalPages]);

  const handleNextPage = useCallback(() => {
    if (totalPagesRef.current !== null) {
      setCurrentPage((prev) => Math.min(totalPagesRef.current!, prev + 1));
    }
  }, []);

  // Memoised so useCitationSync's stable ref picks up the same reference
  // across renders and doesn't re-trigger the re-seed effect unnecessarily.
  const handlePageChange = useCallback((page: number) => setCurrentPage(page), []);

  // Bidirectional citation ↔ page sync
  const {
    activeCitationId,
    citationClickVersion,
    highlightBox: syncHighlightBox,
    highlightPage: syncHighlightPage,
    handleCitationClick,
  } = useCitationSync({
    citations,
    currentPage,
    onPageChange: handlePageChange,
    initialHighlightBox: highlightBox,
    initialPage,
    initialCitationId,
  });

  const paginationControls: PaginationControls = {
    currentPage,
    totalPages,
    onPageChange: handlePageChange,
    onTotalPagesDetected: handleTotalPagesDetected,
    scale: pdfScale,
    onScaleChange: setPdfScale,
  };

  return (
    <Flex
      direction="column"
      style={{
        height: '100%',
        width: '100%',
        overflow: 'hidden',
        background: 'var(--color-background)',
        ...style,
      }}
    >
      {/* ── Header ── */}
      <Flex
        align="center"
        justify="between"
        style={{
          padding: '10px 12px 10px 14px',
          flexShrink: 0,
          borderBottom: '1px solid var(--olive-3)',
          background: 'var(--olive-2)',
        }}
      >
        <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
          <FileIcon
            extension={resolvePreviewIconExtension(recordDetails, file.type)}
            filename={file.name}
            mimeType={file.type}
            size={ICON_SIZES.FILE_ICON_LARGE}
            fallbackIcon="description"
          />
          <Text
            size="2"
            weight="medium"
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              color: 'var(--slate-12)',
            }}
          >
            {file.name}
          </Text>
        </Flex>

        <Flex align="center" gap="1">
          {canDownload && (
            <IconButton
              variant="ghost"
              color="gray"
              size="2"
              onClick={() =>
                downloadPreviewFile({ name: file.name, url: file.url, blob: file.blob })
              }
              title="Download"
            >
              <MaterialIcon name="download" size={ICON_SIZES.FILE_ICON_SMALL} color="var(--slate-11)" />
            </IconButton>
          )}

          {onToggleFullscreen && (
            <IconButton
              variant="ghost"
              color="gray"
              size="2"
              onClick={onToggleFullscreen}
              title="Open in fullscreen"
            >
              <MaterialIcon name="open_in_full" size={ICON_SIZES.FILE_ICON_SMALL} color="var(--slate-11)" />
            </IconButton>
          )}

          {onClose && (
            <IconButton variant="ghost" color="gray" size="2" onClick={onClose} aria-label="Close preview">
              <MaterialIcon name="close" size={ICON_SIZES.FILE_ICON_SMALL} color="var(--slate-11)" />
            </IconButton>
          )}
        </Flex>
      </Flex>

      {/* ── Tabs ── */}
      <Box
        style={{
          flexShrink: 0,
          paddingTop: 'var(--space-4)',
          paddingLeft: 'var(--space-4)',
          paddingRight: 'var(--space-4)',
        }}
      >
        <FilePreviewTabs tabs={tabs} activeTab={activeTab} onTabChange={handleTabChange} />
      </Box>

      {/* ── Content area (tab body + optional citations column) ──
          `position: relative` so the left-edge resize handle can use absolute positioning.
          `display: flex; flexDirection: column` so the inner row can use flex: 1 + minHeight: 0. */}
      <Box
        style={{
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          position: 'relative',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <Flex style={{ flex: 1, minHeight: 0, minWidth: 0, overflow: 'hidden', alignItems: 'stretch' }}>
          {/* Main preview / details body */}
          <Box
            style={{
              flex: 1,
              minHeight: 0,
              minWidth: 0,
              position: 'relative',
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
              paddingLeft: 'var(--space-4)',
              paddingRight: 'var(--space-4)',
              paddingBottom: 'var(--space-2)',
              paddingTop: 'var(--space-2)',
            }}
          >
            {/* Tab content scroll area */}
            <Box
              style={{
                flex: 1,
                minHeight: 0,
                height: '100%',
                width: '100%',
                maxWidth: '100%',
                minWidth: 0,
                overflow: 'auto',
                boxSizing: 'border-box',
              }}
              className="file-preview-scroll-area"
            >
              {isLoading ? (
                <Flex
                  align="center"
                  justify="center"
                  direction="column"
                  gap="3"
                  style={{ height: '100%' }}
                >
                  <LottieLoader variant="loader" size={40} showLabel />
                </Flex>
              ) : hasError && activeTab === 'preview' ? (
                <Flex
                  direction="column"
                  align="center"
                  justify="center"
                  gap="3"
                  style={{ height: '100%', padding: 'var(--space-6)' }}
                >
                  <MaterialIcon name="error_outline" size={48} color="var(--red-9)" />
                  <Text size="3" weight="medium" color="red">
                    {error}
                  </Text>
                </Flex>
              ) : activeTab === 'preview' ? (
                <FilePreviewRenderer
                  fileUrl={file.url}
                  fileName={file.name}
                  fileType={file.type}
                  fileBlob={file.blob}
                  webUrl={file.webUrl}
                  previewRenderable={file.previewRenderable}
                  pagination={paginationControls}
                  highlightBox={hasCitations ? syncHighlightBox : highlightBox}
                  highlightPage={hasCitations ? syncHighlightPage : undefined}
                  citations={hasCitations ? citations : undefined}
                  activeCitationId={hasCitations ? activeCitationId : undefined}
                  citationClickVersion={hasCitations ? citationClickVersion : undefined}
                  onHighlightClick={
                    hasCitations
                      ? (id: string) => {
                          const citation = citations?.find((c) => c.id === id);
                          if (citation) handleCitationClick(citation);
                        }
                      : undefined
                  }
                />
              ) : activeTab === 'file-details' ? (
                <FileDetailsTab recordDetails={recordDetails ?? null} />
              ) : null}
            </Box>

            {/* Floating pagination / zoom controls */}
            {activeTab === 'preview' && paginationVisibility.shouldShow && (
              <Flex
                align="center"
                justify="center"
                gap="2"
                style={{
                  position: 'absolute',
                  bottom: 'var(--space-4)',
                  left: '50%',
                  transform: 'translateX(-50%)',
                  padding: 'var(--space-2)',
                  backgroundColor: 'var(--color-panel-solid)',
                  border: '1px solid var(--slate-3)',
                  borderRadius: 'var(--radius-1)',
                  boxShadow: '0px 20px 28px 0px rgba(0, 0, 0, 0.15)',
                  zIndex: 20,
                  isolation: 'isolate',
                  pointerEvents: 'auto',
                }}
              >
                <IconButton
                  variant="ghost"
                  color="gray"
                  size="1"
                  onClick={handlePdfZoomOut}
                  disabled={pdfScale <= PDF_ZOOM_MIN}
                  style={{ width: '24px', height: '24px', padding: 0 }}
                  aria-label="Zoom out"
                >
                  <MaterialIcon name="remove" size={ICON_SIZES.SECONDARY} />
                </IconButton>

                <Box style={{ minWidth: '40px', textAlign: 'center' }}>
                  <Text as="span" size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
                    {Math.round(pdfScale * 100)}%
                  </Text>
                </Box>

                <IconButton
                  variant="ghost"
                  color="gray"
                  size="1"
                  onClick={handlePdfZoomIn}
                  disabled={pdfScale >= PDF_ZOOM_MAX}
                  style={{ width: '24px', height: '24px', padding: 0 }}
                  aria-label="Zoom in"
                >
                  <MaterialIcon name="add" size={ICON_SIZES.SECONDARY} />
                </IconButton>

                <IconButton
                  variant="ghost"
                  color="gray"
                  size="1"
                  onClick={toggleZoomLock}
                  style={{ width: '24px', height: '24px', padding: 0 }}
                  title={isZoomLocked ? t('filePreview.unlockZoom') : t('filePreview.lockZoom')}
                  aria-label={isZoomLocked ? t('filePreview.unlockZoom') : t('filePreview.lockZoom')}
                  aria-pressed={isZoomLocked}
                >
                  <MaterialIcon
                    name={isZoomLocked ? 'lock' : 'lock_open'}
                    size={ICON_SIZES.SECONDARY}
                    color={isZoomLocked ? 'var(--accent-9)' : undefined}
                  />
                </IconButton>

                <Box
                  style={{
                    width: '1px',
                    height: '16px',
                    backgroundColor: 'var(--slate-4)',
                    flexShrink: 0,
                  }}
                  aria-hidden
                />

                <IconButton
                  variant="ghost"
                  color="gray"
                  size="1"
                  onClick={handlePrevPage}
                  disabled={currentPage === 1}
                  style={{ width: '24px', height: '24px', padding: 0 }}
                >
                  <MaterialIcon name="chevron_left" size={ICON_SIZES.SECONDARY} />
                </IconButton>

                <Box
                  style={{
                    backgroundColor: 'var(--slate-2)',
                    border: '1px solid var(--slate-3)',
                    borderRadius: 'var(--radius-1)',
                    padding: '4px 12px',
                  }}
                >
                  <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                    {totalPages === null ? `${currentPage}/?` : `${currentPage}/${totalPages}`}
                  </Text>
                </Box>

                <IconButton
                  variant="ghost"
                  color="gray"
                  size="1"
                  onClick={handleNextPage}
                  disabled={totalPages === null || currentPage === totalPages}
                  style={{ width: '24px', height: '24px', padding: 0 }}
                >
                  <MaterialIcon name="chevron_right" size={ICON_SIZES.SECONDARY} />
                </IconButton>
              </Flex>
            )}
          </Box>

          {/* Citations panel — only on the Preview tab */}
          {hasCitations && activeTab === 'preview' && (
            <>
              <Box
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize citations panel"
                onPointerDown={beginCitationsSplitResize}
                style={{
                  width: '6px',
                  flexShrink: 0,
                  alignSelf: 'stretch',
                  cursor: 'col-resize',
                  touchAction: 'none',
                  borderLeft: '1px solid var(--olive-3)',
                  backgroundColor: 'transparent',
                }}
                onPointerEnter={(ev) => {
                  ev.currentTarget.style.backgroundColor = 'var(--olive-4)';
                }}
                onPointerLeave={(ev) => {
                  ev.currentTarget.style.backgroundColor = 'transparent';
                }}
              />
              <Box
                style={{
                  width: `${citationsWidthPx}px`,
                  flexShrink: 0,
                  minWidth: 0,
                  height: '100%',
                  minHeight: 0,
                  display: 'flex',
                  flexDirection: 'column',
                }}
              >
                <CitationsPanel
                  citations={citations}
                  activeCitationId={activeCitationId}
                  onCitationClick={handleCitationClick}
                />
              </Box>
            </>
          )}
        </Flex>

        {/* Left-edge resize strip — only rendered when inside the Dialog shell */}
        {showLeftEdgeResizeHandle && onPointerDownLeftEdgeResize && (
          <Box
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize file preview panel"
            onPointerDown={onPointerDownLeftEdgeResize}
            style={{
              position: 'absolute',
              left: 0,
              top: 0,
              bottom: 0,
              width: '6px',
              zIndex: 20,
              cursor: 'col-resize',
              touchAction: 'none',
            }}
            onPointerEnter={(ev) => {
              ev.currentTarget.style.backgroundColor = 'var(--olive-5)';
            }}
            onPointerLeave={(ev) => {
              ev.currentTarget.style.backgroundColor = 'transparent';
            }}
          />
        )}
      </Box>
    </Flex>
  );
}
