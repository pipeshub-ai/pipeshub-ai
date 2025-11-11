import type { Theme } from '@mui/material';
import type { CustomCitation } from 'src/types/chat-bot';
import type { Position, HighlightType, ProcessedCitation } from 'src/types/pdf-highlighter';
import type {
  SearchResult,
  DocumentContent,
} from 'src/sections/knowledgebase/types/search-response';

import { Icon } from '@iconify/react';
import alertCircleIcon from '@iconify-icons/mdi/alert-circle-outline';
import React, { useRef, useState, useEffect, useCallback } from 'react';
import zoomInIcon from '@iconify-icons/mdi/magnify-plus-outline';
import zoomOutIcon from '@iconify-icons/mdi/magnify-minus-outline';
import resetIcon from '@iconify-icons/mdi/restore';
import fitScreenIcon from '@iconify-icons/mdi/fit-to-screen';

import { styled } from '@mui/material/styles';
import {
  Box,
  Paper,
  alpha,
  useTheme,
  Typography,
  IconButton,
  CircularProgress,
} from '@mui/material';

import CitationSidebar from './highlighter-sidebar';
import { createScrollableContainerStyle } from '../utils/styles/scrollbar';

// Props type definition
type ImageHighlighterProps = {
  citations: DocumentContent[] | CustomCitation[];
  url: string | null;
  buffer?: ArrayBuffer | null;
  alt?: string;
  sx?: Record<string, unknown>;
  highlightCitation?: SearchResult | CustomCitation | null;
  onClosePdf: () => void;
};

const ViewerContainer = styled(Box)(({ theme }) => ({
  width: '100%',
  height: '100%',
  position: 'relative',
  overflow: 'hidden',
  borderRadius: theme.shape.borderRadius,
  border: `1px solid ${theme.palette.divider}`,
}));

const LoadingOverlay = styled(Box)(({ theme }) => ({
  position: 'absolute',
  top: 0,
  left: 0,
  width: '100%',
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  backgroundColor: 'rgba(255, 255, 255, 0.8)',
  zIndex: 10,
}));

const ErrorOverlay = styled(Box)(({ theme }) => ({
  position: 'absolute',
  top: 0,
  left: 0,
  width: '100%',
  height: '100%',
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  backgroundColor: theme.palette.error.lighter,
  color: theme.palette.error.dark,
  padding: theme.spacing(2),
  textAlign: 'center',
  zIndex: 10,
}));

const ImageContainer = styled(Box)(({ theme }) => ({
  width: '100%',
  height: '100%',
  overflow: 'auto',
  minHeight: '100px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.default, 0.4)
      : theme.palette.background.paper,
  position: 'relative',
}));

const ZoomControls = styled(Box)(({ theme }) => ({
  position: 'absolute',
  top: 16,
  right: 16,
  display: 'flex',
  flexDirection: 'column',
  gap: 8,
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.background.paper, 0.9)
      : alpha(theme.palette.background.paper, 0.95),
  borderRadius: theme.shape.borderRadius,
  padding: 8,
  boxShadow: theme.shadows[4],
  zIndex: 5,
}));

const StyledImage = styled('img')({
  maxWidth: '100%',
  maxHeight: '100%',
  objectFit: 'contain',
  display: 'block',
  transition: 'transform 0.2s ease',
  cursor: 'grab',
  '&:active': {
    cursor: 'grabbing',
  },
});

const HighlightOverlay = styled(Box)(({ theme }) => ({
  position: 'absolute',
  border: `2px solid ${alpha(theme.palette.primary.main, 0.8)}`,
  backgroundColor: alpha(theme.palette.primary.main, 0.15),
  cursor: 'pointer',
  transition: 'all 0.2s ease',
  pointerEvents: 'auto',
  borderRadius: 4,
  boxShadow: `0 0 0 1px ${alpha(theme.palette.primary.main, 0.3)}`,

  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.25),
    boxShadow: `0 0 0 2px ${alpha(theme.palette.primary.main, 0.5)}`,
    borderWidth: 3,
    zIndex: 2,
  },

  '&.active': {
    backgroundColor: alpha(theme.palette.primary.main, 0.3),
    boxShadow: `0 0 0 3px ${alpha(theme.palette.primary.main, 0.6)}`,
    borderWidth: 3,
    borderColor: theme.palette.primary.main,
    zIndex: 3,
    animation: 'highlightPulse 0.8s ease-out 1',
  },

  '@keyframes highlightPulse': {
    '0%': {
      boxShadow: `0 0 0 3px ${alpha(theme.palette.primary.main, 0.6)}`,
    },
    '50%': {
      boxShadow: `0 0 0 6px ${alpha(theme.palette.primary.main, 0.4)}`,
    },
    '100%': {
      boxShadow: `0 0 0 3px ${alpha(theme.palette.primary.main, 0.6)}`,
    },
  },
}));

const HighlightLabel = styled(Box)(({ theme }) => ({
  position: 'absolute',
  top: -8,
  left: -8,
  backgroundColor:
    theme.palette.mode === 'dark'
      ? alpha(theme.palette.primary.main, 0.9)
      : theme.palette.primary.main,
  color: theme.palette.primary.contrastText,
  padding: '2px 8px',
  borderRadius: 4,
  fontSize: '11px',
  fontWeight: 600,
  whiteSpace: 'nowrap',
  boxShadow: theme.shadows[2],
  pointerEvents: 'none',
}));

// Helper functions
const getNextId = (): string => `img-hl-${Math.random().toString(36).substring(2, 10)}`;

const isDocumentContent = (
  citation: DocumentContent | CustomCitation
): citation is DocumentContent => 'metadata' in citation && citation.metadata !== undefined;

const normalizeText = (text: string | null | undefined): string => {
  if (!text) return '';
  return text.trim().replace(/\s+/g, ' ');
};

const processImageHighlight = (
  citation: DocumentContent | CustomCitation,
  index: number
): HighlightType | null => {
  try {
    const rawContent = citation.content;
    const normalizedContent = normalizeText(rawContent);

    if (!normalizedContent || normalizedContent.length < 5) {
      return null;
    }

    let id: string;
    if ('highlightId' in citation && citation.highlightId) id = citation.highlightId as string;
    else if ('id' in citation && citation.id) id = citation.id as string;
    else if ('citationId' in citation && citation.citationId) id = citation.citationId as string;
    else if (isDocumentContent(citation) && citation.metadata?._id) id = citation.metadata._id;
    else if ('_id' in citation && citation._id) id = citation._id as string;
    else id = getNextId();

    // Create a position that will be used for overlay placement
    // We'll calculate actual positions based on image dimensions later
    const position: Position = {
      pageNumber: 1, // Images are single "page"
      boundingRect: {
        x1: 10 + index * 5, // Offset each highlight slightly
        y1: 10 + index * 5,
        x2: 30 + index * 5,
        y2: 30 + index * 5,
        width: 20,
        height: 20,
      },
      rects: [],
    };

    return {
      content: { text: normalizedContent },
      position,
      comment: { text: `Citation ${index + 1}`, emoji: '📌' },
      id,
    };
  } catch (error) {
    console.error('Error processing highlight for citation:', citation, error);
    return null;
  }
};

const ImageHighlighter: React.FC<ImageHighlighterProps> = ({
  url,
  buffer,
  alt = 'Image',
  sx = {},
  citations = [],
  highlightCitation = null,
  onClosePdf,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const imageContainerRef = useRef<HTMLDivElement | null>(null);
  const blobUrlRef = useRef<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string>('');
  const [imageDimensions, setImageDimensions] = useState({ width: 0, height: 0 });
  const [zoom, setZoom] = useState<number>(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });
  const [processedCitations, setProcessedCitations] = useState<ProcessedCitation[]>([]);
  const [highlightedCitationId, setHighlightedCitationId] = useState<string | null>(null);
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const fullScreenContainerRef = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  const scrollableStyles = createScrollableContainerStyle(theme);

  // Load image
  useEffect(() => {
    // Cleanup previous blob URL if it exists
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }

    const loadImage = async () => {
      try {
        setLoading(true);
        setError(null);

        let loadedUrl = '';
        if (url) {
          loadedUrl = url;
        } else if (buffer) {
          // Convert buffer to blob URL
          const blob = new Blob([buffer], { type: 'image/png' });
          loadedUrl = URL.createObjectURL(blob);
          blobUrlRef.current = loadedUrl;
        } else {
          throw new Error('Either url or buffer must be provided');
        }

        setImageUrl(loadedUrl);
        setLoading(false);
      } catch (err: any) {
        console.error('Error loading image:', err);
        setError(err.message || 'Failed to load image');
        setLoading(false);
      }
    };

    loadImage();

    return () => {
      // Cleanup blob URLs
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, [url, buffer]);

  // Handle image load to get dimensions
  const handleImageLoad = useCallback((e: React.SyntheticEvent<HTMLImageElement>) => {
    const img = e.currentTarget;
    setImageDimensions({
      width: img.naturalWidth,
      height: img.naturalHeight,
    });
  }, []);

  // Process citations
  useEffect(() => {
    if (citations && citations.length > 0 && imageDimensions.width > 0) {
      const processed: ProcessedCitation[] = citations
        .map((citation, index) => {
          const highlight = processImageHighlight(citation, index);
          if (highlight) {
            return { ...citation, highlight } as ProcessedCitation;
          }
          return null;
        })
        .filter((item): item is ProcessedCitation => item !== null);

      setProcessedCitations(processed);
    } else {
      setProcessedCitations([]);
    }
  }, [citations, imageDimensions]);

  // Zoom controls
  const handleZoomIn = useCallback(() => {
    setZoom((prev) => Math.min(prev + 0.25, 5));
  }, []);

  const handleZoomOut = useCallback(() => {
    setZoom((prev) => Math.max(prev - 0.25, 0.5));
  }, []);

  const handleResetZoom = useCallback(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, []);

  const handleFitScreen = useCallback(() => {
    if (imageContainerRef.current && imageRef.current) {
      const containerWidth = imageContainerRef.current.clientWidth;
      const containerHeight = imageContainerRef.current.clientHeight;
      const imageWidth = imageRef.current.naturalWidth;
      const imageHeight = imageRef.current.naturalHeight;

      const scaleX = containerWidth / imageWidth;
      const scaleY = containerHeight / imageHeight;
      const newZoom = Math.min(scaleX, scaleY, 1);

      setZoom(newZoom);
      setPan({ x: 0, y: 0 });
    }
  }, []);

  // Pan controls
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (zoom > 1) {
        setIsPanning(true);
        setPanStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
      }
    },
    [zoom, pan]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (isPanning) {
        setPan({
          x: e.clientX - panStart.x,
          y: e.clientY - panStart.y,
        });
      }
    },
    [isPanning, panStart]
  );

  const handleMouseUp = useCallback(() => {
    setIsPanning(false);
  }, []);

  // Handle highlight click
  const handleHighlightClick = useCallback((highlightId: string) => {
    setHighlightedCitationId(highlightId);
    const highlightElement = document.querySelector(`[data-highlight-id="${highlightId}"]`);
    if (highlightElement) {
      highlightElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, []);

  // Scroll to highlight from sidebar
  const scrollToHighlight = useCallback((highlight: HighlightType | null): void => {
    if (!highlight || !highlight.id) return;

    setHighlightedCitationId(highlight.id);
    setTimeout(() => {
      const highlightElement = document.querySelector(`[data-highlight-id="${highlight.id}"]`);
      if (highlightElement) {
        highlightElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }, 100);
  }, []);

  // Fullscreen handling
  const handleFullscreenChange = useCallback((): void => {
    setIsFullscreen(!!document.fullscreenElement);
  }, []);

  useEffect(() => {
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, [handleFullscreenChange]);

  const toggleFullScreen = useCallback(async (): Promise<void> => {
    try {
      if (!document.fullscreenElement && fullScreenContainerRef.current) {
        await fullScreenContainerRef.current.requestFullscreen();
      } else {
        await document.exitFullscreen();
      }
    } catch (err) {
      console.error('Error toggling fullscreen:', err);
    }
  }, []);

  // Calculate highlight positions based on current image size
  const getHighlightStyle = useCallback((highlight: HighlightType) => {
    if (!imageRef.current) return {};

    const imgRect = imageRef.current.getBoundingClientRect();
    const containerRect = imageContainerRef.current?.getBoundingClientRect();

    if (!containerRect) return {};

    // Calculate actual image display size
    const displayWidth = imgRect.width;
    const displayHeight = imgRect.height;

    // Calculate position relative to container
    const highlightWidth = 150; // Fixed width for highlights
    const highlightHeight = 100; // Fixed height for highlights

    // Position highlights in a grid pattern
    const { x1, y1 } = highlight.position.boundingRect;
    const left = (x1 / 100) * displayWidth;
    const top = (y1 / 100) * displayHeight;

    return {
      left: `${left}px`,
      top: `${top}px`,
      width: `${highlightWidth}px`,
      height: `${highlightHeight}px`,
    };
  }, []);

  return (
    <ViewerContainer ref={fullScreenContainerRef} component={Paper} sx={sx}>
      {loading && (
        <LoadingOverlay>
          <CircularProgress size={40} sx={{ mb: 2 }} />
          <Typography variant="body1">Loading Image...</Typography>
        </LoadingOverlay>
      )}

      {error && !loading && (
        <ErrorOverlay>
          <Icon icon={alertCircleIcon} style={{ fontSize: 40, marginBottom: 16 }} />
          <Typography variant="h6">Loading Error</Typography>
          <Typography variant="body1">{error}</Typography>
        </ErrorOverlay>
      )}

      {/* Container for main content and sidebar */}
      <Box
        sx={{
          display: 'flex',
          height: '100%',
          width: '100%',
          visibility: loading || error ? 'hidden' : 'visible',
        }}
      >
        {/* Image Content Area */}
        <Box
          sx={{
            height: '100%',
            flexGrow: 1,
            width: processedCitations.length > 0 ? 'calc(100% - 280px)' : '100%',
            transition: 'width 0.3s ease-in-out',
            position: 'relative',
            borderRight:
              processedCitations.length > 0
                ? (themeVal: Theme) => `1px solid ${themeVal.palette.divider}`
                : 'none',
          }}
        >
          {/* Zoom Controls */}
          <ZoomControls>
            <IconButton size="small" onClick={handleZoomIn} title="Zoom In">
              <Icon icon={zoomInIcon} style={{ fontSize: 20 }} />
            </IconButton>
            <IconButton size="small" onClick={handleZoomOut} title="Zoom Out">
              <Icon icon={zoomOutIcon} style={{ fontSize: 20 }} />
            </IconButton>
            <IconButton size="small" onClick={handleFitScreen} title="Fit to Screen">
              <Icon icon={fitScreenIcon} style={{ fontSize: 20 }} />
            </IconButton>
            <IconButton size="small" onClick={handleResetZoom} title="Reset">
              <Icon icon={resetIcon} style={{ fontSize: 20 }} />
            </IconButton>
            <Typography
              variant="caption"
              sx={{
                textAlign: 'center',
                px: 1,
                py: 0.5,
                bgcolor: alpha(theme.palette.background.default, 0.5),
                borderRadius: 1,
              }}
            >
              {Math.round(zoom * 100)}%
            </Typography>
          </ZoomControls>

          <ImageContainer
            ref={imageContainerRef}
            sx={{ ...scrollableStyles }}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
          >
            {imageUrl && (
              <Box
                sx={{
                  position: 'relative',
                  transform: `scale(${zoom}) translate(${pan.x / zoom}px, ${pan.y / zoom}px)`,
                  transformOrigin: 'center center',
                  transition: isPanning ? 'none' : 'transform 0.2s ease',
                }}
              >
                <StyledImage
                  ref={imageRef}
                  src={imageUrl}
                  alt={alt}
                  onLoad={handleImageLoad}
                  draggable={false}
                />
              </Box>
            )}
          </ImageContainer>
        </Box>

        {/* Sidebar Area (Conditional) */}
        {processedCitations.length > 0 && !loading && !error && (
          <Box
            sx={{
              width: '280px',
              height: '100%',
              flexShrink: 0,
              overflowY: 'auto',
            }}
          >
            <CitationSidebar
              citations={processedCitations}
              scrollViewerTo={scrollToHighlight}
              highlightedCitationId={highlightedCitationId}
              toggleFullScreen={toggleFullScreen}
              onClosePdf={onClosePdf}
            />
          </Box>
        )}
      </Box>
    </ViewerContainer>
  );
};

export default ImageHighlighter;
