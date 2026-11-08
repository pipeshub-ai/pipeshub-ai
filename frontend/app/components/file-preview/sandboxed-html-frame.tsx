'use client';

import { useCallback, useEffect, useMemo, useRef } from 'react';
import type { PreviewCitation } from './types';
import {
  PREVIEW_SANDBOX,
  SET_ACTIVE_CITATION,
  buildPreviewDocument,
  createPreviewNonce,
  parseCitationClick,
} from './sandboxed-html';

interface SandboxedHtmlFrameProps {
  /** Already passed through `sanitizePreviewHtml`. */
  sanitizedHtml: string;
  title: string;
  dark: boolean;
  citations?: PreviewCitation[];
  activeCitationId?: string | null;
  onHighlightClick?: (citationId: string) => void;
  baseCss?: string;
}

export function SandboxedHtmlFrame({
  sanitizedHtml,
  title,
  dark,
  citations,
  activeCitationId,
  onHighlightClick,
  baseCss,
}: SandboxedHtmlFrameProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const activeRef = useRef(activeCitationId);
  const onClickRef = useRef(onHighlightClick);

  useEffect(() => {
    onClickRef.current = onHighlightClick;
  }, [onHighlightClick]);

  // Keyed on what the highlights use, not the array's identity: a rebuild
  // reloads the frame, losing its scroll position and the active citation.
  const citationsKey = JSON.stringify((citations ?? []).map((c) => [c.id, c.content]));

  const srcDoc = useMemo(() => {
    const highlighted = (JSON.parse(citationsKey) as Array<[string, string]>).map(
      ([id, content]): PreviewCitation => ({ id, content }),
    );
    return buildPreviewDocument({ sanitizedHtml, nonce: createPreviewNonce(), dark, citations: highlighted, baseCss });
  }, [sanitizedHtml, dark, citationsKey, baseCss]);

  const knownIds = useMemo(
    () => new Set((JSON.parse(citationsKey) as Array<[string, string]>).map(([id]) => id)),
    [citationsKey],
  );

  // The frame's origin is opaque ('null'), so '*' is the only usable target;
  // only a citation id is ever sent.
  const postActive = useCallback(() => {
    iframeRef.current?.contentWindow?.postMessage(
      { type: SET_ACTIVE_CITATION, id: activeRef.current ?? null },
      '*',
    );
  }, []);

  useEffect(() => {
    activeRef.current = activeCitationId;
    postActive();
  }, [activeCitationId, postActive]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const frameWindow = iframeRef.current?.contentWindow;
      if (!frameWindow || event.source !== frameWindow) return;
      const id = parseCitationClick(event.data, knownIds);
      if (id) onClickRef.current?.(id);
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [knownIds]);

  return (
    <iframe
      ref={iframeRef}
      title={title}
      sandbox={PREVIEW_SANDBOX}
      srcDoc={srcDoc}
      referrerPolicy="no-referrer"
      onLoad={postActive}
      style={{
        display: 'block',
        width: '100%',
        height: '100%',
        minHeight: '60vh',
        border: 'none',
        background: 'transparent',
      }}
    />
  );
}
