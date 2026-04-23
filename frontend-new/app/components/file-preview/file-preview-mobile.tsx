'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { Box, Flex, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { FilePreviewRenderer } from './renderers/file-preview-renderer';
import { FileDetailsTab } from './file-details-tab';
import { CitationCard } from './citations-panel';
import { useTranslation } from 'react-i18next';
import { useCitationSync } from './use-citation-sync';
import { shouldShowPagination } from './utils';
import type { FilePreviewProps, PaginationControls, PreviewCitation } from './types';

export function FilePreviewMobile({
  file,
  onOpenChange,
  isLoading = false,
  error,
  recordDetails,
  initialPage,
  highlightBox,
  citations,
  initialCitationId,
}: FilePreviewProps) {
  const { t } = useTranslation();
  const hasCitations = citations && citations.length > 0;
  const hasError = !isLoading && !!error;
  const [showFileInfo, setShowFileInfo] = useState(false);
  const [showCitationsSheet, setShowCitationsSheet] = useState(false);
  const [currentPage, setCurrentPage] = useState(initialPage ?? 1);
  const [totalPages, setTotalPages] = useState<number | null>(null);

  const cardRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  // Pagination visibility
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

  const handleTotalPagesDetected = useCallback((numPages: number) => {
    setTotalPages(numPages);
  }, []);

  const handlePageChange = (page: number) => {
    setCurrentPage(page);
  };

  const handlePrevPage = () => {
    setCurrentPage(prev => Math.max(1, prev - 1));
  };

  const handleNextPage = () => {
    if (totalPages !== null) {
      setCurrentPage(prev => Math.min(totalPages, prev + 1));
    }
  };

  // Bidirectional citation <-> page sync
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

  // Auto-scroll active citation into view in bottom sheet
  useEffect(() => {
    if (!activeCitationId || !showCitationsSheet) return;
    const timer = setTimeout(() => {
      const el = cardRefs.current.get(activeCitationId);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [activeCitationId, showCitationsSheet]);

  const handleCitationCardClick = (citation: PreviewCitation) => {
    handleCitationClick(citation);
  };

  const paginationControls: PaginationControls = {
    currentPage,
    totalPages,
    onPageChange: handlePageChange,
    onTotalPagesDetected: handleTotalPagesDetected,
  };

  const handleClose = () => {
    onOpenChange?.(false);
  };

  return (
    <Box
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 100,
        display: 'flex',
        flexDirection: 'column',
        background: 'linear-gradient(180deg, var(--olive-2) 0%, var(--olive-1) 100%)',
      }}
    >
      {/* Header */}
      <Flex
        align="center"
        justify="between"
        style={{
          height: '40px',
          padding: '0 var(--space-2)',
          backdropFilter: 'blur(8px)',
          backgroundColor: 'var(--effects-translucent)',
          borderBottom: '1px solid var(--olive-3)',
          flexShrink: 0,
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
            size="1"
            weight="medium"
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              maxWidth: '144px',
              color: 'var(--neutral-12)',
            }}
          >
            {file.name}
          </Text>
          <IconButton
            variant="ghost"
            color="gray"
            size="1"
            onClick={() => setShowFileInfo(true)}
            title={t('filePreview.fileInformation')}
            style={{ flexShrink: 0 }}
          >
            <MaterialIcon name="info" size={16} color="var(--slate-11)" />
          </IconButton>
        </Flex>

        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          onClick={handleClose}
          title={t('common.close')}
        >
          <MaterialIcon name="close" size={16} color="var(--slate-11)" />
        </IconButton>
      </Flex>

      {/* Content */}
      <Flex direction="column" style={{ flex: 1, minHeight: 0, overflow: 'hidden', position: 'relative' }}>
        {showFileInfo ? (
          /* File Information View */
          <Flex
            direction="column"
            style={{ height: '100%', overflow: 'auto' }}
            className="no-scrollbar"
          >
            {/* Back button header */}
            <Flex
              align="center"
              gap="1"
              style={{
                padding: 'var(--space-5) var(--space-4)',
                flexShrink: 0,
              }}
            >
              <IconButton
                variant="ghost"
                color="gray"
                size="1"
                onClick={() => setShowFileInfo(false)}
              >
                <MaterialIcon name="keyboard_arrow_left" size={16} />
              </IconButton>
              <Text size="2" weight="medium" style={{ color: 'var(--neutral-12)' }}>
                {t('filePreview.fileInformation')}
              </Text>
            </Flex>

            {/* File details */}
            <Box style={{ padding: '0 var(--space-4) var(--space-4)', flex: 1 }}>
              <FileDetailsTab recordDetails={recordDetails ?? null} />
            </Box>
          </Flex>
        ) : (
          /* Document Preview */
          <Box
            style={{
              height: showCitationsSheet ? '45vh' : '100%',
              width: '100%',
              maxWidth: '100%',
              minWidth: 0,
              overflow: 'auto',
              position: 'relative',
              transition: 'height 0.2s ease',
              boxSizing: 'border-box',
            }}
            className="no-scrollbar"
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
            ) : hasError ? (
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
            ) : (
              <Box
                style={{
                  height: '100%',
                  width: '100%',
                  maxWidth: '100%',
                  minWidth: 0,
                  padding: 'var(--space-2)',
                  boxSizing: 'border-box',
                }}
              >
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
                  onHighlightClick={hasCitations ? (id: string) => {
                    const citation = citations?.find((c) => c.id === id);
                    if (citation) handleCitationCardClick(citation);
                  } : undefined}
                />
              </Box>
            )}

            {/* Floating pagination controls */}
            {paginationVisibility.shouldShow && !showCitationsSheet && (
              <Flex
                align="center"
                justify="center"
                gap="2"
                style={{
                  position: 'absolute',
                  bottom: hasCitations ? '64px' : 'var(--space-4)',
                  left: '50%',
                  transform: 'translateX(-50%)',
                  padding: 'var(--space-1)',
                  backgroundColor: 'var(--effects-translucent)',
                  border: '1px solid var(--olive-3)',
                  borderRadius: 'var(--radius-1)',
                  boxShadow: '0px 20px 28px 0px rgba(0, 0, 0, 0.15)',
                  backdropFilter: 'blur(25px)',
                  zIndex: 1,
                }}
              >
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
                    backgroundColor: 'var(--olive-2)',
                    border: '1px solid var(--olive-3)',
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

            {/* "View Citations" floating button */}
            {hasCitations && !showCitationsSheet && (
              <Flex
                align="center"
                justify="center"
                style={{
                  position: 'absolute',
                  bottom: 'var(--space-4)',
                  left: '50%',
                  transform: 'translateX(-50%)',
                  backdropFilter: 'blur(25px)',
                  backgroundColor: 'var(--effects-translucent)',
                  border: '1px solid var(--olive-3)',
                  borderRadius: 'var(--radius-1)',
                  boxShadow: '0px 20px 28px 0px rgba(0, 0, 0, 0.15)',
                  padding: 'var(--space-1) var(--space-2)',
                  gap: 'var(--space-2)',
                  cursor: 'pointer',
                  zIndex: 1,
                }}
                onClick={() => setShowCitationsSheet(true)}
              >
                <Text size="1" style={{ color: 'var(--accent-12)', whiteSpace: 'nowrap' }}>
                  View Citations
                </Text>
              </Flex>
            )}
          </Box>
        )}

        {/* Citations Bottom Sheet */}
        {hasCitations && showCitationsSheet && !showFileInfo && (
          <Box
            style={{
              flexShrink: 0,
              height: '55vh',
              backdropFilter: 'blur(25px)',
              backgroundColor: 'var(--olive-2)',
              borderTop: '1px solid var(--olive-3)',
              borderTopLeftRadius: 'var(--radius-2)',
              borderTopRightRadius: 'var(--radius-2)',
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
              padding: 'var(--space-4)',
            }}
          >
            {/* Sheet Header */}
            <Flex
              align="center"
              justify="between"
              style={{ flexShrink: 0, marginBottom: 'var(--space-2)' }}
            >
              <Text size="1" weight="medium" style={{ color: 'var(--neutral-a12)' }}>
                {t('chat.citations')}
              </Text>
              <IconButton
                variant="ghost"
                color="gray"
                size="2"
                onClick={() => setShowCitationsSheet(false)}
              >
                <MaterialIcon name="close" size={16} />
              </IconButton>
            </Flex>

            {/* Citation Cards */}
            <Flex
              direction="column"
              gap="2"
              className="no-scrollbar"
              style={{ flex: 1, overflow: 'auto'}}
            >
              {citations!.map((citation, index) => (
                <CitationCard
                  key={citation.id}
                  ref={(el: HTMLDivElement | null) => {
                    if (el) cardRefs.current.set(citation.id, el);
                    else cardRefs.current.delete(citation.id);
                  }}
                  citation={citation}
                  index={index + 1}
                  isActive={activeCitationId === citation.id}
                  onClick={() => handleCitationCardClick(citation)}
                />
              ))}
            </Flex>
          </Box>
        )}
      </Flex>
    </Box>
  );
}
