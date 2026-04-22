'use client';

import { useState, useEffect, useCallback } from 'react';
import { Box, Flex, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';

import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { FilePreviewRenderer } from './renderers/file-preview-renderer';
import { CitationsPanel } from './citations-panel';
import { useCitationSync } from './use-citation-sync';
import { shouldShowPagination } from './utils';
import type { FilePreviewProps, PaginationControls } from './types';
import { useCitationsColumnResize } from './use-citations-column-resize';

export function FilePreviewFullscreen({
  source: _source,
  file,
  defaultTab: _defaultTab = 'preview',
  onClose,
  onExitFullscreen,
  isLoading = false,
  error,
  recordDetails: _recordDetails,
  initialPage,
  highlightBox,
  citations,
  initialCitationId,
}: FilePreviewProps) {
  const hasCitations = citations && citations.length > 0;
  const hasError = !isLoading && !!error;
  const { citationsWidthPx, beginCitationsSplitResize } = useCitationsColumnResize();
  const [currentPage, setCurrentPage] = useState(initialPage ?? 1);
  const [totalPages, setTotalPages] = useState<number | null>(null);

  // Calculate pagination visibility
  const paginationVisibility = shouldShowPagination(
    file.type,
    file.name,
    totalPages,
    isLoading,
    false
  );

  // Reset pagination when file changes
  useEffect(() => {
    setCurrentPage(initialPage ?? 1);
    setTotalPages(null);
  }, [file.id, file.url, initialPage]);

  // Handle page detection callback from renderer
  const handleTotalPagesDetected = useCallback((numPages: number) => {
    setTotalPages(numPages);
  }, []);

  const handlePrevPage = () => {
    setCurrentPage(prev => Math.max(1, prev - 1));
  };

  const handleNextPage = () => {
    if (totalPages !== null) {
      setCurrentPage(prev => Math.min(totalPages, prev + 1));
    }
  };

  const handlePageChange = (page: number) => {
    setCurrentPage(page);
  };

  // Bidirectional citation ↔ page sync
  const {
    activeCitationId,
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

  // Create pagination controls object
  const paginationControls: PaginationControls = {
    currentPage,
    totalPages,
    onPageChange: handlePageChange,
    onTotalPagesDetected: handleTotalPagesDetected,
  };

  const handleHighlightClick = useCallback(
    (id: string) => {
      const citation = citations?.find((c) => c.id === id);
      if (citation) handleCitationClick(citation);
    },
    [citations, handleCitationClick]
  );

  return (
    <Box
      style={{
        position: 'fixed',
        inset: 0,
        minHeight: 0,
        backgroundColor: 'var(--color-background)',
        display: 'flex',
        flexDirection: 'column',
        zIndex: 100,
      }}
    >
      {/* Header */}
      <Flex
        align="center"
        justify="between"
        style={{
          padding: 'var(--space-2) var(--space-3)',
          borderBottom: '1px solid var(--slate-6)',
          backdropFilter: 'blur(8px)',
          backgroundColor: 'var(--color-panel-translucent)',
          height: '40px',
        }}
      >
        <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
          <FileIcon
            filename={file.name}
            mimeType={file.type}
            size={16}
            fallbackIcon="description"
          />
          <Text 
            size="2" 
            weight="medium"
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {file.name}
          </Text>
        </Flex>

        <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
          {onExitFullscreen && (
            <IconButton
              variant="ghost"
              color="gray"
              size="1"
              onClick={onExitFullscreen}
              title="Exit full screen"
            >
              <MaterialIcon name="close_fullscreen" size={ICON_SIZES.HEADER} />
            </IconButton>
          )}
          <IconButton
            variant="ghost"
            color="gray"
            size="1"
            onClick={onClose}
            title="Close"
          >
            <MaterialIcon name="close" size={ICON_SIZES.HEADER} />
          </IconButton>
        </Flex>
      </Flex>

      {/* Main Content Area */}
      <Flex style={{ flex: 1, overflow: 'hidden', minHeight: 0, minWidth: 0, alignItems: 'stretch' }}>
        {/* Left Side - Document Preview */}
        <Box
          style={{
            flex: 1,
            minWidth: 0,
            minHeight: 0,
            position: 'relative',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'stretch',
            justifyContent: 'flex-start',
            padding: 'var(--space-6)',
            overflow: 'auto',
            background: 'linear-gradient(180deg, var(--slate-2) 0%, var(--slate-1) 100%)',
            boxSizing: 'border-box',
          }}
          className="no-scrollbar"
        >
          {isLoading ? (
            <Flex align="center" justify="center" style={{ flex: 1, minHeight: '12rem' }}>
              <div className="loading-spinner" />
            </Flex>
          ) : hasError ? (
            <Flex
              direction="column"
              align="center"
              justify="center"
              gap="3"
              style={{ padding: 'var(--space-6)' }}
            >
              <MaterialIcon name="error_outline" size={48} color="var(--red-9)" />
              <Text size="3" weight="medium" color="red">
                {error}
              </Text>
            </Flex>
          ) : (
            <Box style={{ width: '100%', maxWidth: '100%', minWidth: 0, flex: 1, minHeight: 0 }}>
              <FilePreviewRenderer
                fileUrl={file.url}
                fileName={file.name}
                fileType={file.type}
                fileBlob={file.blob}
                pagination={paginationControls}
                highlightBox={hasCitations ? syncHighlightBox : highlightBox}
                highlightPage={hasCitations ? syncHighlightPage : undefined}
                citations={hasCitations ? citations : undefined}
                activeCitationId={hasCitations ? activeCitationId : undefined}
                onHighlightClick={hasCitations ? handleHighlightClick : undefined}
              />
            </Box>
          )}

          {/* Floating Pagination - Bottom Center */}
          {paginationVisibility.shouldShow && (
            <Flex
              align="center"
              justify="center"
              gap="2"
              style={{
                position: 'absolute',
                bottom: 'var(--space-6)',
                left: '50%',
                transform: 'translateX(-50%)',
                padding: 'var(--space-2)',
                backgroundColor: 'var(--color-panel-solid)',
                border: '1px solid var(--slate-3)',
                borderRadius: 'var(--radius-1)',
                boxShadow: '0px 20px 28px 0px rgba(0, 0, 0, 0.15)',
              }}
            >
              <IconButton
                variant="ghost"
                color="gray"
                size="1"
                onClick={handlePrevPage}
                disabled={currentPage === 1}
                style={{
                  width: '24px',
                  height: '24px',
                  padding: 0,
                }}
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
                style={{
                  width: '24px',
                  height: '24px',
                  padding: 0,
                }}
              >
                <MaterialIcon name="chevron_right" size={ICON_SIZES.SECONDARY} />
              </IconButton>
            </Flex>
          )}
        </Box>

        {/* Citations — resizable column */}
        {hasCitations && (
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
                widthPx={citationsWidthPx}
              />
            </Box>
          </>
        )}


      </Flex>
    </Box>
  );
}
