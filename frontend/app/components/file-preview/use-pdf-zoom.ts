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
  // Until the person zooms, the viewer fits the page to the panel and reports that scale back.
  const [zoomChosen, setZoomChosen] = useState(false);
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
      setZoomChosen(true);
    }
    setIsHydrated(true);
  }, []);

  useEffect(() => {
    if (!isHydrated || isZoomLockedRef.current) return;
    setPdfScale(PDF_ZOOM_DEFAULT);
    setZoomChosen(false);
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
    setZoomChosen(true);
    setPdfScale((s) =>
      Math.min(
        PDF_ZOOM_MAX,
        Math.round((s + PDF_ZOOM_STEP) * PDF_ZOOM_PRECISION_FACTOR) / PDF_ZOOM_PRECISION_FACTOR,
      ),
    );
  }, []);

  const handlePdfZoomOut = useCallback(() => {
    setZoomChosen(true);
    setPdfScale((s) =>
      Math.max(
        PDF_ZOOM_MIN,
        Math.round((s - PDF_ZOOM_STEP) * PDF_ZOOM_PRECISION_FACTOR) / PDF_ZOOM_PRECISION_FACTOR,
      ),
    );
  }, []);

  return {
    pdfScale,
    /** The scale to apply, or undefined to fit the page width (the person has not zoomed). */
    pdfScaleValue: zoomChosen ? pdfScale : undefined,
    setPdfScale,
    handlePdfZoomIn,
    handlePdfZoomOut,
    isZoomLocked,
    toggleZoomLock,
  };
}
