'use client';

import { useState, useEffect, useLayoutEffect, useCallback, useRef } from 'react';
import { Dialog, VisuallyHidden } from '@radix-ui/themes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { FilePreviewMobile } from './file-preview-mobile';
import { FilePreviewInlinePanel } from './file-preview-inline-panel';
import type { FilePreviewProps } from './types';
import {
  PANEL_MAX_PX,
  PANEL_MIN_PX,
  PANEL_WIDTH_LS_KEY,
  readSavedPanelWidthPx,
  clamp,
  viewportMaxPanelPx,
} from './resize-storage';

/**
 * Desktop file-preview sidebar — a resizable Dialog panel that slides in from
 * the right. On mobile it delegates to `FilePreviewMobile` instead.
 *
 * The panel body is rendered by `FilePreviewInlinePanel`, which is also used
 * directly in the chat split-pane layout without any Dialog wrapper.
 */
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
  hideFileDetails,
  showDownload,
}: FilePreviewProps) {
  const isMobile = useIsMobile();
  const hasCitations = citations && citations.length > 0;

  const [panelWidthPx, setPanelWidthPx] = useState(() => readSavedPanelWidthPx(hasCitations));
  const panelWidthRef = useRef(panelWidthPx);
  useLayoutEffect(() => {
    panelWidthRef.current = panelWidthPx;
  }, [panelWidthPx]);

  // Keep panel width within viewport on resize (passive listener — useEffect is correct here)
  useEffect(() => {
    if (!open) return;
    const onResize = () => {
      setPanelWidthPx((w) => Math.min(w, viewportMaxPanelPx()));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [open]);

  const beginPanelEdgeResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = panelWidthRef.current;
    const maxW = Math.min(PANEL_MAX_PX, viewportMaxPanelPx());
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    let finalW = startW;
    const move = (ev: PointerEvent) => {
      finalW = clamp(startW + (startX - ev.clientX), PANEL_MIN_PX, maxW);
      setPanelWidthPx(finalW);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      try {
        localStorage.setItem(PANEL_WIDTH_LS_KEY, String(finalW));
      } catch {
        /* ignore */
      }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }, []);

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
        hideFileDetails={hideFileDetails}
        showDownload={showDownload}
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
          width: `${panelWidthPx}px`,
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
            Preview pane for {file.name}. Document content, file details and related citations are
            shown here.
          </Dialog.Description>
        </VisuallyHidden>

        <FilePreviewInlinePanel
          source={source}
          file={file}
          defaultTab={defaultTab}
          onClose={() => onOpenChange?.(false)}
          onToggleFullscreen={onToggleFullscreen}
          isLoading={isLoading}
          error={error}
          recordDetails={recordDetails}
          initialPage={initialPage}
          highlightBox={highlightBox}
          citations={citations}
          initialCitationId={initialCitationId}
          hideFileDetails={hideFileDetails}
          showDownload={showDownload}
          showLeftEdgeResizeHandle
          onPointerDownLeftEdgeResize={beginPanelEdgeResize}
          style={{
            borderRadius: 0,
            background: 'transparent',
            backdropFilter: 'none',
          }}
        />
      </Dialog.Content>
    </Dialog.Root>
  );
}
