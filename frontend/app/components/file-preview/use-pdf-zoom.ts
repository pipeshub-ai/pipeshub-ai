'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  PDF_ZOOM_DEFAULT,
  PDF_ZOOM_MAX,
  PDF_ZOOM_MIN,
  PDF_ZOOM_PRECISION_FACTOR,
  PDF_ZOOM_STEP,
} from './types';

export const ZOOM_LOCK_LS_KEY = 'ph.filePreview.lockedZoomScale';

function readLockedZoomScale(): number | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(ZOOM_LOCK_LS_KEY);
    if (raw === null) return null;
    const n = Number(raw);
    if (!Number.isFinite(n)) return null;
    return Math.max(PDF_ZOOM_MIN, Math.min(PDF_ZOOM_MAX, n));
  } catch {
    return null;
  }
}

function saveLockedZoomScale(scale: number): void {
  try {
    localStorage.setItem(ZOOM_LOCK_LS_KEY, String(scale));
  } catch {
    /* ignore */
  }
}

function clearLockedZoomScale(): void {
  try {
    localStorage.removeItem(ZOOM_LOCK_LS_KEY);
  } catch {
    /* ignore */
  }
}

export function usePdfZoom(fileId: string, fileUrl: string, initialPage?: number) {
  const [pdfScale, setPdfScale] = useState(PDF_ZOOM_DEFAULT);
  const [isZoomLocked, setIsZoomLocked] = useState(false);
  const [isHydrated, setIsHydrated] = useState(false);

  const isZoomLockedRef = useRef(isZoomLocked);
  useEffect(() => {
    isZoomLockedRef.current = isZoomLocked;
  }, [isZoomLocked]);

  useEffect(() => {
    const savedScale = readLockedZoomScale();
    if (savedScale !== null) {
      setPdfScale(savedScale);
      setIsZoomLocked(true);
    }
    setIsHydrated(true);
  }, []);

  useEffect(() => {
    if (!isHydrated || isZoomLockedRef.current) return;
    setPdfScale(PDF_ZOOM_DEFAULT);
  }, [fileId, fileUrl, initialPage, isHydrated]);

  useEffect(() => {
    if (!isHydrated) return;
    if (isZoomLocked) {
      saveLockedZoomScale(pdfScale);
    } else {
      clearLockedZoomScale();
    }
  }, [isZoomLocked, pdfScale, isHydrated]);

  const toggleZoomLock = useCallback(() => {
    setIsZoomLocked((locked) => !locked);
  }, []);

  const handlePdfZoomIn = useCallback(() => {
    setPdfScale((s) =>
      Math.min(
        PDF_ZOOM_MAX,
        Math.round((s + PDF_ZOOM_STEP) * PDF_ZOOM_PRECISION_FACTOR) / PDF_ZOOM_PRECISION_FACTOR,
      ),
    );
  }, []);

  const handlePdfZoomOut = useCallback(() => {
    setPdfScale((s) =>
      Math.max(
        PDF_ZOOM_MIN,
        Math.round((s - PDF_ZOOM_STEP) * PDF_ZOOM_PRECISION_FACTOR) / PDF_ZOOM_PRECISION_FACTOR,
      ),
    );
  }, []);

  return {
    pdfScale,
    setPdfScale,
    handlePdfZoomIn,
    handlePdfZoomOut,
    isZoomLocked,
    toggleZoomLock,
  };
}
