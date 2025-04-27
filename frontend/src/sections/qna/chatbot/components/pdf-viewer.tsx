import { Icon } from '@iconify/react';
import plusIcon from '@iconify-icons/mdi/plus';
import minusIcon from '@iconify-icons/mdi/minus';
import closeIcon from '@iconify-icons/mdi/close';
import { Page, pdfjs, Document } from 'react-pdf';
import refreshIcon from '@iconify-icons/mdi/refresh';
import { useResizeObserver } from '@wojtekmaj/react-hooks';
import { useMemo, useState, useEffect, useCallback } from 'react';
import fileDocIcon from '@iconify-icons/mdi/file-document-outline';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle-outline';

import {
  Box,
  Fade,
  Stack,
  Dialog,
  Tooltip,
  Skeleton,
  IconButton,
  Typography,
  DialogTitle,
  DialogContent,
  CircularProgress,
} from '@mui/material';

pdfjs.GlobalWorkerOptions.workerSrc = `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjs.version}/pdf.worker.min.mjs`;

const resizeObserverOptions = {};
const maxWidth = 800;

interface PDFViewerProps {
  open: boolean;
  onClose: () => void;
  pdfUrl?: string | null;
  pdfBuffer?: ArrayBuffer | null;
  fileName: string;
}

interface DocumentLoadSuccess {
  numPages: number;
}

const PageLoader = () => (
  <Box sx={{ width: '100%', display: 'flex', justifyContent: 'center' }}>
    <Box sx={{ width: maxWidth, position: 'relative' }}>
      <Skeleton
        variant="rectangular"
        width="100%"
        height={500}
        sx={{
          borderRadius: 1,
          bgcolor: 'grey.100',
        }}
      />
      <Box
        sx={{
          position: 'absolute',
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          display: 'flex',
          alignItems: 'center',
          gap: 1,
        }}
      >
        <CircularProgress size={20} thickness={4} />
        <Typography variant="body2" color="text.secondary">
          Loading document...
        </Typography>
      </Box>
    </Box>
  </Box>
);

// Helper function to safely convert ArrayBuffer to Uint8Array
const safeBufferToUint8Array = (buffer: ArrayBuffer): Uint8Array => {
  // Clone the buffer to avoid detachment issues
  const bufferClone = buffer.slice(0);
  return new Uint8Array(bufferClone);
};

export default function PDFViewer({ open, onClose, pdfUrl, pdfBuffer, fileName }: PDFViewerProps) {
  const [numPages, setNumPages] = useState<number>(0);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [containerRef, setContainerRef] = useState<HTMLElement | null>(null);
  const [containerWidth, setContainerWidth] = useState<number | undefined>();
  const [loading, setLoading] = useState<boolean>(true);
  const [scale, setScale] = useState<number>(1);
  const [error, setError] = useState<string | null>(null);
  const [key, setKey] = useState<number>(0);

  // Memoize the Document options to prevent unnecessary reloads
  const documentOptions = useMemo(
    () => ({
      cMapUrl: `//cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjs.version}/cmaps/`,
      cMapPacked: true,
    }),
    []
  );

  // This state will store the properly prepared PDF source for react-pdf
  const [pdfSource, setPdfSource] = useState<any>(null);

  // Process the input sources to create the appropriate format for react-pdf
  useEffect(() => {
    let isMounted = true;

    const prepareSource = async () => {
      try {
        setLoading(true);
        setError(null);

        if (pdfUrl) {
          // For URL-based PDFs, pass the URL directly
          setPdfSource({ url: pdfUrl });
        } else if (pdfBuffer) {
          // Create a safer copy of the buffer in a way that prevents detachment issues
          // by performing the operation asynchronously
          await new Promise((resolve) => setTimeout(resolve, 0));

          if (!isMounted) return;

          try {
            // Use the helper function to safely convert buffer
            const uint8Array = safeBufferToUint8Array(pdfBuffer);
            setPdfSource({ data: uint8Array });
          } catch (err) {
            console.error('Error preparing PDF buffer:', err);
            setError('Failed to prepare PDF data. The buffer may be corrupted.');
            setLoading(false);
          }
        } else {
          setPdfSource(null);
          setLoading(false);
        }

        // Reset viewing state
        setNumPages(0);
        setCurrentPage(1);
        setScale(1);
        setKey((prev) => prev + 1);
      } catch (err) {
        console.error('Error in prepareSource:', err);
        if (isMounted) {
          setError('An unexpected error occurred while preparing the document.');
          setLoading(false);
        }
      }
    };

    prepareSource();

    return () => {
      isMounted = false;
    };
  }, [pdfUrl, pdfBuffer]);

  // Cleanup function
  useEffect(
    () => () => {
      setNumPages(0);
      setCurrentPage(1);
      setScale(1);
      setError(null);
      setLoading(true);
      setPdfSource(null);
    },
    []
  );

  const onResize = useCallback((entries: ResizeObserverEntry[]) => {
    const [entry] = entries;
    if (entry) {
      setContainerWidth(entry.contentRect.width);
    }
  }, []);

  useResizeObserver(containerRef, resizeObserverOptions, onResize);

  const onDocumentLoadSuccess = useCallback(({ numPages: nextNumPages }: DocumentLoadSuccess) => {
    setNumPages(nextNumPages);
    setLoading(false);
    setError(null);
  }, []);

  const onDocumentLoadError = useCallback((err: Error) => {
    console.error('Error loading PDF:', err);
    setError('Failed to load PDF. Please try again.');
    setLoading(false);
  }, []);

  const handleZoomIn = useCallback(() => {
    setScale((prevScale) => Math.min(prevScale + 0.1, 2));
  }, []);

  const handleZoomOut = useCallback(() => {
    setScale((prevScale) => Math.max(prevScale - 0.1, 0.5));
  }, []);

  const handleRetry = useCallback(() => {
    setLoading(true);
    setError(null);
    setKey((prev) => prev + 1);
  }, []);

  if (!pdfSource) {
    return null;
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="lg"
      fullWidth
      TransitionComponent={Fade}
      PaperProps={{
        sx: {
          minHeight: '90vh',
          maxHeight: '90vh',
          bgcolor: 'background.default',
        },
      }}
    >
      <DialogTitle
        sx={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          borderBottom: '1px solid',
          borderColor: 'divider',
          p: 2,
          bgcolor: 'background.paper',
        }}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <Icon icon={fileDocIcon} style={{ fontSize: '24px' }} />
          <Typography variant="h6" noWrap>
            {fileName}
          </Typography>
          {loading && <CircularProgress size={16} thickness={4} sx={{ ml: 2 }} />}
        </Stack>
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <Tooltip title="Zoom out">
            <IconButton onClick={handleZoomOut} size="small" disabled={scale <= 0.5 || loading}>
              <Icon icon={minusIcon} />
            </IconButton>
          </Tooltip>
          <Typography
            variant="body2"
            sx={{
              minWidth: '40px',
              textAlign: 'center',
              color: 'text.secondary',
            }}
          >
            {Math.round(scale * 100)}%
          </Typography>
          <Tooltip title="Zoom in">
            <IconButton onClick={handleZoomIn} size="small" disabled={scale >= 2 || loading}>
              <Icon icon={plusIcon} />
            </IconButton>
          </Tooltip>
          <Tooltip title="Close">
            <IconButton onClick={onClose} size="small" sx={{ ml: 1 }}>
              <Icon icon={closeIcon} />
            </IconButton>
          </Tooltip>
        </Box>
      </DialogTitle>

      <DialogContent sx={{ p: 0 }}>
        <Box
          ref={setContainerRef}
          sx={{
            overflow: 'auto',
            height: '100%',
            px: 2,
            py: 3,
            bgcolor: 'grey.50',
          }}
        >
          {error ? (
            <Box
              sx={{
                p: 4,
                textAlign: 'center',
                borderRadius: 2,
                bgcolor: 'error.lighter',
                color: 'error.main',
                mt: 4,
              }}
            >
              <Icon icon={alertCircleIcon} style={{ fontSize: '48px' }} />
              <Typography variant="h6" sx={{ mt: 2, color: 'error.main' }}>
                {error}
              </Typography>
              <Typography variant="body2" color="error.dark" sx={{ mt: 1, mb: 2 }}>
                {pdfUrl
                  ? 'The document might be inaccessible or the URL might have expired'
                  : 'The document might be corrupted or in an unsupported format'}
              </Typography>
              <Tooltip title="Try loading again">
                <IconButton
                  onClick={handleRetry}
                  color="primary"
                  sx={{
                    bgcolor: 'background.paper',
                    '&:hover': { bgcolor: 'background.paper' },
                  }}
                >
                  <Icon icon={refreshIcon} />
                </IconButton>
              </Tooltip>
            </Box>
          ) : (
            <Document
              key={key}
              file={pdfSource}
              onLoadSuccess={onDocumentLoadSuccess}
              onLoadError={onDocumentLoadError}
              loading={<PageLoader />}
              options={documentOptions}
            >
              {Array.from(new Array(numPages), (_, index) => (
                <Fade in={!loading} key={`page_${index + 1}`}>
                  <Box
                    sx={{
                      display: 'flex',
                      justifyContent: 'center',
                      mb: 2,
                      '&:last-child': { mb: 0 },
                      boxShadow: '0 4px 24px rgba(0,0,0,0.06)',
                      borderRadius: 1,
                      bgcolor: 'background.paper',
                      overflow: 'hidden',
                    }}
                  >
                    <Page
                      pageNumber={index + 1}
                      width={
                        containerWidth
                          ? Math.min(containerWidth * scale, maxWidth * scale)
                          : maxWidth * scale
                      }
                      loading={<PageLoader />}
                      renderAnnotationLayer={false}
                      renderTextLayer={false}
                    />
                  </Box>
                </Fade>
              ))}
            </Document>
          )}
        </Box>
      </DialogContent>
    </Dialog>
  );
}
