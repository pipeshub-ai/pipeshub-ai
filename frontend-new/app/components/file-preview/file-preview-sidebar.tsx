'use client';

import { useState, useEffect, useCallback } from 'react';
import { Box, Flex, Text, IconButton, Dialog, VisuallyHidden } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { FilePreviewMobile } from './file-preview-mobile';
import { FilePreviewTabs } from './file-preview-tabs';
import { FilePreviewRenderer } from './renderers/file-preview-renderer';
import { FileDetailsTab } from './file-details-tab';
import { CitationsPanel } from './citations-panel';
import { useCitationSync } from './use-citation-sync';
import { getTabsForSource, shouldShowPagination } from './utils';
import type { FilePreviewProps, FilePreviewTab, PaginationControls } from './types';

export function FilePreviewSidebar({
  open,
  source,
  file,
  defaultTab = 'preview',
  onOpenChange,
  onToggleFullscreen,
  isLoading = false,
  error,
  recordDetails,
  initialPage,
  highlightBox,
  citations,
  initialCitationId,
}: FilePreviewProps) {
  const isMobile = useIsMobile();
  const hasCitations = citations && citations.length > 0;
  const hasError = !isLoading && !!error;
  const [activeTab, setActiveTab] = useState<FilePreviewTab>(defaultTab);
  const [currentPage, setCurrentPage] = useState(initialPage ?? 1);
  const [totalPages, setTotalPages] = useState<number | null>(null); // null = detecting
  const tabs = getTabsForSource(source);

  // Calculate pagination visibility
  const paginationVisibility = shouldShowPagination(
    file.type,
    file.name,
    totalPages,
    isLoading,
    false // error state
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

  const handleChatClick = () => {
    // TODO: Implement chat functionality
    console.log('Chat button clicked');
  };

  const handleTabChange = (tab: FilePreviewTab) => {
    setActiveTab(tab);
  };

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

  // Mobile: render full-screen mobile preview instead of Dialog sidebar
  if (isMobile) {
    return (
      <FilePreviewMobile
        open={open}
        source={source}
        file={file}
        defaultTab={defaultTab}
        onOpenChange={onOpenChange}
        onToggleFullscreen={onToggleFullscreen}
        isLoading={isLoading}
        error={error}
        recordDetails={recordDetails}
        initialPage={initialPage}
        highlightBox={highlightBox}
        citations={citations}
        initialCitationId={initialCitationId}
      />
    );
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content
        style={{
          position: 'fixed',
          top: 10,
          right: 10,
          bottom: 10,
          width: hasCitations ? '860px' : '37.5rem',
          maxWidth: '100vw',
          maxHeight: 'calc(100vh - 20px)',
          padding: 0,
          margin: 0,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          transform: 'none',
          animation: 'slideInFromRight 0.2s ease-out',
          borderRadius: 'var(--Radius-2-max, 4px)',
          border: '1px solid var(--olive-3)',
          background: 'var(--effects-translucent)',
          boxShadow: '0 20px 48px 0 rgba(0, 0, 0, 0.25)',
          backdropFilter: 'blur(25px)',
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>{file.name}</Dialog.Title>
          <Dialog.Description>
            Preview pane for {file.name}. Document content, file details and
            related citations are shown here.
          </Dialog.Description>
        </VisuallyHidden>
        {/* Header */}
        <Flex
          align="center"
          justify="between"
          style={{
            padding: '12px 12px 12px 16px',
            flexShrink: 0,
            borderBottom: '1px solid var(--olive-3)',
            background: 'var(--effects-translucent)',
            backdropFilter: 'blur(8px)',
          }}
        >
        <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
          <FileIcon
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

          <IconButton
            variant="ghost"
            color="gray"
            size="2"
            onClick={() => onOpenChange?.(false)}
          >
            <MaterialIcon name="close" size={ICON_SIZES.FILE_ICON_SMALL} color="var(--slate-11)" />
          </IconButton>
        </Flex>
        </Flex>

        {/* Tabs - Full Width */}
        <Box 
          style={{ 
            flexShrink: 0,
            paddingTop: 'var(--space-4)',
            paddingLeft: 'var(--space-4)',
            paddingRight: 'var(--space-4)',
            // borderBottom: '1px solid var(--slate-6)',
          }}
        >
          <FilePreviewTabs tabs={tabs} activeTab={activeTab} onTabChange={handleTabChange} />
        </Box>

        {/* Content Area with Floating Controls + optional Citations Panel */}
        <Flex style={{ flex: 1, overflow: 'hidden' }}>
          {/* Main preview / details content */}
          <Box 
            className="no-scrollbar"
            style={{ 
                flex: 1, 
                position: 'relative',
                overflow: 'hidden',
                paddingLeft: 'var(--space-4)',
                paddingRight: 'var(--space-4)',
                paddingBottom: 'var(--space-2)',
                paddingTop: 'var(--space-2)',
                minWidth: 0,
              }}
            >
              {/* Tab Content */}
              <Box 
                style={{ 
                  height: '100%',
                  overflow: 'auto',
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
                    pagination={paginationControls}
                    highlightBox={hasCitations ? syncHighlightBox : highlightBox}
                    highlightPage={hasCitations ? syncHighlightPage : undefined}
                    citations={hasCitations ? citations : undefined}
                    activeCitationId={hasCitations ? activeCitationId : undefined}
                    onHighlightClick={hasCitations ? (id: string) => {
                      const citation = citations?.find((c) => c.id === id);
                      if (citation) handleCitationClick(citation);
                    } : undefined}
                  />
                ) : activeTab === 'file-details' ? (
                  <FileDetailsTab recordDetails={recordDetails ?? null} />
                ) : null}
              </Box>

              {/* Floating Pagination Controls - Only show when appropriate */}
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

                  <Box
                    style={{
                      backgroundColor: 'var(--accent-a3)',
                      borderRadius: 'var(--radius-2)',
                      padding: '8px 12px',
                      height: '32px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      cursor: 'pointer',
                    }}
                    onClick={handleChatClick}
                  >
                    <MaterialIcon name="chat" size={ICON_SIZES.SECONDARY} color="var(--accent-11)" />
                    <Text size="2" weight="medium" style={{ color: 'var(--accent-11)' }}>
                      Chat
                    </Text>
                  </Box>
                </Flex>
              )}
          </Box>

          {/* Citations Panel — only when citations are provided */}
          {hasCitations && (
            <Box
              style={{
                borderLeft: '1px solid var(--olive-3)',
                height: '100%',
              }}
            >
              <CitationsPanel
                citations={citations}
                activeCitationId={activeCitationId}
                onCitationClick={handleCitationClick}
              />
            </Box>
          )}
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
