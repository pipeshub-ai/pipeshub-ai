'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  PDF_ZOOM_DEFAULT,
  PDF_ZOOM_MAX,
  PDF_ZOOM_MIN,
  PDF_ZOOM_PRECISION_FACTOR,
  PDF_ZOOM_STEP,
} from './types';

export function usePdfZoom(fileId: string, fileUrl: string, initialPage?: number) {
  const [pdfScale, setPdfScale] = useState(PDF_ZOOM_DEFAULT);

  useEffect(() => {
    setPdfScale(PDF_ZOOM_DEFAULT);
  }, [fileId, fileUrl, initialPage]);

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

  return { pdfScale, setPdfScale, handlePdfZoomIn, handlePdfZoomOut };
}
