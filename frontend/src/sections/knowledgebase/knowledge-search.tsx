import type { IconifyIcon } from '@iconify/react';

import { Icon } from '@iconify/react';
import { useNavigate } from 'react-router';
import closeIcon from '@iconify-icons/mdi/close';
import refreshIcon from '@iconify-icons/mdi/refresh';
import eyeIcon from '@iconify-icons/mdi/eye-outline';
import magnifyIcon from '@iconify-icons/mdi/magnify';
import lightBulbIcon from '@iconify-icons/mdi/lightbulb-outline';
import fileSearchIcon from '@iconify-icons/mdi/file-search-outline';
import React, { useRef, useState, useEffect, useCallback } from 'react';

import {
  Box,
  Card,
  Chip,
  Paper,
  alpha,
  Button,
  Divider,
  Tooltip,
  useTheme,
  Skeleton,
  TextField,
  IconButton,
  Typography,
  CardContent,
  InputAdornment,
  CircularProgress,
} from '@mui/material';

import { ORIGIN } from './constants/knowledge-search';
import { createScrollableContainerStyle } from '../qna/chatbot/utils/styles/scrollbar';

import type { SearchResult, KnowledgeSearchProps } from './types/search-response';

const VIEWABLE_EXTENSIONS = ['pdf', 'xlsx', 'xls', 'csv', 'docx', 'html', 'txt', 'md', 'mdx', 'ppt', 'pptx', 'jpg', 'jpeg', 'png', 'webp', 'svg'];

// Helper function to get file icon color based on extension
export const getFileIconColor = (extension: string): string => {
  const ext = extension?.toLowerCase() || '';

  switch (ext) {
    case 'pdf':
      return '#f44336';
    case 'doc':
    case 'docx':
      return '#2196f3';
    case 'xls':
    case 'xlsx':
      return '#4caf50';
    case 'ppt':
    case 'pptx':
      return '#ff9800';
    case 'mail':
    case 'email':
      return '#9C27B0';
    default:
      return '#1976d2';
  }
};

// Helper function to format date strings
export const formatDate = (dateString: string): string => {
  if (!dateString) return 'N/A';

  try {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch (e) {
    return 'N/A';
  }
};

// Generate a truncated preview of the content
export const getContentPreview = (content: string, maxLength: number = 220): string => {
  if (!content) return '';
  return content.length > maxLength ? `${content.substring(0, maxLength)}...` : content;
};

// Get source icon based on origin/connector - now uses dynamic connector data
export const getSourceIcon = (result: SearchResult, allConnectors: any[]): string => {
  if (!result?.metadata) {
    return '/assets/icons/connectors/default.svg';
  }

  // Find connector data dynamically
  const connector = allConnectors.find(
    (c) =>
      c.name.toUpperCase() === result.metadata.connector?.toUpperCase() ||
      c.name === result.metadata.connector
  );

  // If connector found, use its iconPath
  if (connector?.iconPath) {
    return connector.iconPath;
  }

  return '/assets/icons/connectors/default.svg';
};

// Helper for highlighting search text
export const highlightText = (text: string, query: string, theme: any) => {
  if (!query || !text) return text;

  try {
    const parts = text.split(new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'));

    return parts.map((part, index) =>
      part.toLowerCase() === query.toLowerCase() ? (
        <mark
          key={index}
          style={{
            backgroundColor: alpha(theme.palette.warning.light, 0.4),
            padding: '0 2px',
            borderRadius: '2px',
            color: theme.palette.text.primary,
          }}
        >
          {part}
        </mark>
      ) : (
        part
      )
    );
  } catch (e) {
    return text;
  }
};

function isDocViewable(extension: string): boolean {
  return VIEWABLE_EXTENSIONS.includes(extension?.toLowerCase());
}

interface ActionButtonProps {
  icon: string | IconifyIcon;
  label: string;
  onClick?: () => void;
}

const ActionButton: React.FC<ActionButtonProps> = ({ icon, label, onClick }) => (
  <Button
    variant="outlined"
    onClick={onClick}
    startIcon={<Icon icon={icon} />}
    sx={{
      borderRadius: 1,
      textTransform: 'none',
      fontWeight: 500,
      py: 0.75,
      px: 2,
      fontSize: '0.875rem',
    }}
  >
    {label}
  </Button>
);

// Main KnowledgeSearch component
const KnowledgeSearch = ({
  searchResults,
  loading,
  canLoadMore = true,
  onSearchQueryChange,
  onTopKChange,
  onViewCitations,
  recordsMap,
  allConnectors,
}: KnowledgeSearchProps) => {
  const theme = useTheme();
  const scrollableStyles = createScrollableContainerStyle(theme);
  const [searchInputValue, setSearchInputValue] = useState<string>('');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedRecord, setSelectedRecord] = useState<SearchResult | null>(null);
  const [detailsOpen, setDetailsOpen] = useState<boolean>(false);
  const navigate = useNavigate();
  const observer = useRef<IntersectionObserver | null>(null);
  const [hasSearched, setHasSearched] = useState<boolean>(false);
  const [loadingRecordId, setLoadingRecordId] = useState<string | null>(null);
  const previousQueryRef = useRef<string>('');
  const resultsContainerRef = useRef<HTMLDivElement | null>(null);
  // Synchronize searchQuery with parent component's state
  useEffect(() => {
    if (searchQuery !== searchInputValue) {
      setSearchInputValue(searchQuery);
    }
    // eslint-disable-next-line
  }, [searchQuery]);

  // Scroll to top when search results change due to query change
  useEffect(() => {
    // Only scroll if the query changed (not due to loading more results)
    if (searchQuery && searchQuery !== previousQueryRef.current && searchResults.length > 0) {
      if (resultsContainerRef.current) {
        resultsContainerRef.current.scrollTo({
          top: 0,
          behavior: 'smooth',
        });
      }
      previousQueryRef.current = searchQuery;
    }
  }, [searchResults, searchQuery]);

  const handleViewCitations = (record: SearchResult, event: React.MouseEvent) => {
    event.stopPropagation();

    const recordId = record.metadata?.recordId || '';
    const extension = record.metadata?.extension || '';
    setLoadingRecordId(recordId);

    if (isDocViewable(extension)) {
      if (onViewCitations) {
        onViewCitations(recordId, extension, record).finally(() => {
          setLoadingRecordId(null);
        });
      }
    }
  };

  const lastResultElementRef = useCallback(
    (node: Element | null) => {
      // Stop if:
      // 1. Currently loading
      // 2. Can't load more (reached limit or got fewer results than requested)
      // 3. Have fewer than 10 results (likely all available results shown)
      if (loading || !canLoadMore) return;

      if (observer.current) observer.current.disconnect();

      observer.current = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && onTopKChange) {
          onTopKChange((prevTopK: number) => prevTopK + 10);
        }
      });

      if (node) observer.current.observe(node);
    },
    [loading, onTopKChange, canLoadMore]
  );

  const handleSearch = () => {
    setSearchQuery(searchInputValue);
    setHasSearched(true);
    if (onSearchQueryChange) {
      onSearchQueryChange(searchInputValue);
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchInputValue(e.target.value);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch();
    }
  };

  const clearSearch = () => {
    setSearchInputValue('');
    setSearchQuery('');
    setHasSearched(false);
    previousQueryRef.current = '';
    if (onSearchQueryChange) {
      onSearchQueryChange('');
    }
  };

  const handleRecordClick = (record: SearchResult): void => {
    const { recordId } = record.metadata;
    const recordMeta = recordsMap[recordId];

    if (!recordMeta?.webUrl) return;

    let { webUrl } = recordMeta;

    if (recordMeta.origin === 'UPLOAD' && !webUrl.startsWith('http')) {
      const baseUrl = `${window.location.protocol}//${window.location.host}`;
      webUrl = baseUrl + webUrl;
    }

    window.open(webUrl, '_blank', 'noopener,noreferrer');
  };

  // Show different UI states based on search state
  const showInitialState = !hasSearched && searchResults.length === 0;
  const showNoResultsState = hasSearched && searchResults.length === 0 && !loading;
  const showResultsState = searchResults.length > 0;
  const showLoadingState = loading && !showResultsState;

  return (
    <Box
      sx={{
        height: '100%',
        width: '100%',
        bgcolor: theme.palette.background.default,
        overflow: 'hidden',
        ...scrollableStyles,
      }}
    >
      <Box sx={{ px: 3, py: 3, height: '100%', display: 'flex', flexDirection: 'column' }}>
        {/* Header Section */}
        <Box>
          <Typography variant="h5" sx={{ mb: 1, fontWeight: 600 }}>
            Knowledge Search
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Search across your organization&apos;s knowledge base to find documents, FAQs, and other
            resources
          </Typography>

          {/* Search Bar */}
          <Paper
            sx={{
              p: 1,
              mb: 2,
              boxShadow: 'none',
              borderRadius: 1,
              border: `1px solid ${theme.palette.divider}`,
              backgroundColor: theme.palette.background.paper,
            }}
          >
            <Box sx={{ display: 'flex', gap: 1 }}>
              <TextField
                fullWidth
                value={searchInputValue}
                onChange={handleInputChange}
                onKeyPress={handleKeyPress}
                placeholder="Search for documents, topics, or keywords..."
                variant="outlined"
                size="small"
                sx={{
                  '& .MuiOutlinedInput-root': {
                    borderRadius: 1,
                    fontSize: '0.9rem',
                  },
                  '& .MuiOutlinedInput-notchedOutline': {
                    borderColor: theme.palette.divider,
                  },
                  '& .MuiInputBase-input': {
                    py: 1.25,
                  },
                }}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Icon
                        icon={magnifyIcon}
                        style={{
                          color: theme.palette.text.secondary,
                          fontSize: '1.25rem',
                        }}
                      />
                    </InputAdornment>
                  ),
                  endAdornment: searchInputValue && (
                    <InputAdornment position="end">
                      <IconButton
                        size="small"
                        onClick={() => setSearchInputValue('')}
                        sx={{
                          color: theme.palette.text.secondary,
                          padding: '2px',
                        }}
                      >
                        <Icon
                          icon={closeIcon}
                          style={{
                            fontSize: '1rem',
                          }}
                        />
                      </IconButton>
                    </InputAdornment>
                  ),
                }}
              />
              <Button
                variant="contained"
                onClick={handleSearch}
                disabled={!searchInputValue.trim() || loading}
                sx={{
                  minWidth: '90px',
                  borderRadius: 1,
                  boxShadow: 'none',
                  textTransform: 'none',
                  fontWeight: 500,
                  fontSize: '0.875rem',
                  py: 0.75,
                  px: 2,
                  '&:hover': {
                    boxShadow: 'none',
                    backgroundColor: theme.palette.primary.dark,
                  },
                  '&:disabled': {
                    opacity: 0.7,
                  },
                }}
              >
                {loading ? (
                  <CircularProgress size={18} color="inherit" sx={{ mx: 0.5 }} />
                ) : (
                  'Search'
                )}
              </Button>
            </Box>
          </Paper>
        </Box>

        {/* Results Section - Flexbox to take remaining height */}
        <Box
          sx={{
            flexGrow: 1,
            display: 'flex',
            overflow: 'hidden',
            ...scrollableStyles,
          }}
        >
          {/* Results Column */}
          <Box
            ref={resultsContainerRef}
            sx={{
              width: detailsOpen ? '55%' : '100%',
              overflow: 'auto',
              transition: 'width 0.25s ease-in-out',
              pr: 1,
              ...scrollableStyles,
            }}
          >
            {/* Loading State */}
            {showLoadingState && (
              <Box sx={{ mt: 2 }}>
                {[1, 2, 3].map((item) => (
                  <Paper
                    key={item}
                    elevation={0}
                    sx={{
                      p: 2,
                      mb: 2,
                      borderRadius: '8px',
                      border: `1px solid ${alpha(theme.palette.divider, 0.5)}`,
                    }}
                  >
                    <Box sx={{ display: 'flex', gap: 2 }}>
                      <Skeleton
                        variant="rounded"
                        width={40}
                        height={40}
                        sx={{ borderRadius: '6px' }}
                      />
                      <Box sx={{ flex: 1 }}>
                        <Skeleton variant="text" width="60%" height={24} />
                        <Skeleton variant="text" width="30%" height={20} sx={{ mb: 1 }} />
                        <Skeleton variant="text" width="90%" height={16} />
                        <Skeleton variant="text" width="85%" height={16} />
                        <Skeleton variant="text" width="70%" height={16} />
                      </Box>
                    </Box>
                  </Paper>
                ))}
              </Box>
            )}

            {/* Empty State - No Results */}
            {showNoResultsState && (
              <Box
                sx={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  p: 4,
                  mt: 2,
                  border: `1px solid ${alpha(theme.palette.divider, 0.5)}`,
                  borderRadius: '8px',
                  bgcolor: alpha(theme.palette.background.paper, 0.5),
                }}
              >
                <Icon
                  icon={fileSearchIcon}
                  style={{ fontSize: 48, color: theme.palette.text.secondary, marginBottom: 16 }}
                />
                <Typography variant="h6" sx={{ mb: 1, fontWeight: 500 }}>
                  No results found
                </Typography>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ textAlign: 'center', mb: 2, maxWidth: '400px' }}
                >
                  We couldn&apos;t find any matches for &quot;{searchQuery}&quot;. Try adjusting
                  your search terms or filters.
                </Typography>
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={<Icon icon={refreshIcon} />}
                  onClick={clearSearch}
                  sx={{
                    borderRadius: '6px',
                    textTransform: 'none',
                    px: 2,
                  }}
                >
                  Clear search
                </Button>
              </Box>
            )}

            {/* Empty State - Initial */}
            {showInitialState && (
              <Box
                sx={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  p: 3,
                  mt: 2,
                  border: `1px solid ${theme.palette.divider}`,
                  borderRadius: 1,
                  bgcolor: 'transparent',
                  maxWidth: '520px',
                  mx: 'auto',
                }}
              >
                <Icon
                  icon={lightBulbIcon}
                  style={{
                    fontSize: '2rem',
                    color: theme.palette.primary.main,
                    marginBottom: '16px',
                  }}
                />

                <Typography
                  variant="h6"
                  sx={{
                    mb: 1,
                    fontWeight: 500,
                    fontSize: '1rem',
                  }}
                >
                  Start exploring knowledge
                </Typography>

                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{
                    textAlign: 'center',
                    mb: 3,
                    maxWidth: '400px',
                    lineHeight: 1.5,
                  }}
                >
                  Enter a search term above to discover documents, FAQs, and other resources from
                  your organization&apos;s knowledge base.
                </Typography>
              </Box>
            )}

            {/* Search Results */}
            {showResultsState && (
              <Box sx={{ pt: 1, ...scrollableStyles }}>
                {searchResults.map((result, index) => {
                  if (!result?.metadata) return null;

                  const iconPath = getSourceIcon(result, allConnectors);
                  const fileType = result.metadata.extension?.toUpperCase() || 'DOC';
                  const isViewable = isDocViewable(result.metadata.extension);

                  return (
                    <Card
                      key={result.metadata._id || index}
                      ref={index === searchResults.length - 1 ? lastResultElementRef : null}
                      sx={{
                        mb: 2,
                        cursor: 'pointer',
                        borderRadius: '8px',
                        boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
                        border:
                          selectedRecord?.metadata?._id === result.metadata._id
                            ? `1px solid ${theme.palette.primary.main}`
                            : `1px solid ${alpha(theme.palette.divider, 0.5)}`,
                        '&:hover': {
                          borderColor: theme.palette.primary.main,
                          boxShadow: '0 2px 5px rgba(0,0,0,0.1)',
                        },
                        transition: 'all 0.2s ease-in-out',
                      }}
                      onClick={() => handleRecordClick(result)}
                    >
                      <CardContent sx={{ p: 2 }}>
                        <Box sx={{ display: 'flex', gap: 2 }}>
                          {/* Document Icon */}
                          <Box
                            sx={{
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              width: 40,
                              height: 40,
                              borderRadius: '6px',
                              flexShrink: 0,
                            }}
                          >
                            <Tooltip
                              title={
                                result.metadata.origin === ORIGIN.UPLOAD
                                  ? 'Local KB'
                                  : result.metadata.connector ||
                                    result.metadata.origin ||
                                    'Document'
                              }
                            >
                              <Box sx={{ position: 'relative' }}>
                                <img
                                  src={iconPath}
                                  alt={result.metadata.connector || 'Connector'}
                                  style={{
                                    width: 26,
                                    height: 26,
                                    objectFit: 'contain',
                                  }}
                                  onError={(e) => {
                                    e.currentTarget.style.display = 'none';
                                    e.currentTarget.nextElementSibling?.setAttribute(
                                      'style',
                                      'display: block'
                                    );
                                  }}
                                />
                              </Box>
                            </Tooltip>
                          </Box>

                          {/* Content */}
                          <Box sx={{ flex: 1, minWidth: 0 }}>
                            {/* Header with Title and Meta */}
                            <Box
                              sx={{
                                display: 'flex',
                                justifyContent: 'space-between',
                                alignItems: 'center',
                                width: '100%',
                                gap: 1,
                              }}
                            >
                              {/* Record name with ellipsis for overflow */}
                              <Typography
                                variant="subtitle1"
                                fontWeight={500}
                                sx={{
                                  flexGrow: 1,
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                  minWidth: 0,
                                }}
                              >
                                {result.metadata.recordName || 'Untitled Document'}
                              </Typography>

                              {/* Meta Icons with fixed width */}
                              <Box
                                sx={{
                                  display: 'flex',
                                  gap: 1,
                                  alignItems: 'center',
                                  flexShrink: 0,
                                }}
                              >
                                {isViewable && (
                                  <Button
                                    size="small"
                                    variant="outlined"
                                    startIcon={
                                      loadingRecordId === result.metadata?.recordId ? (
                                        <CircularProgress size={16} color="inherit" />
                                      ) : (
                                        <Icon icon={eyeIcon} />
                                      )
                                    }
                                    onClick={(e) => handleViewCitations(result, e)}
                                    sx={{
                                      fontSize: '0.75rem',
                                      py: 0.5,
                                      height: 24,
                                      whiteSpace: 'nowrap',
                                      textTransform: 'none',
                                      borderRadius: '4px',
                                    }}
                                    disabled={loadingRecordId === result.metadata?.recordId}
                                  >
                                    {loadingRecordId === result.metadata?.recordId
                                      ? 'Loading...'
                                      : 'View Citations'}
                                  </Button>
                                )}

                                <Chip
                                  label={fileType}
                                  size="small"
                                  sx={{
                                    height: 20,
                                    fontSize: '0.7rem',
                                    borderRadius: '4px',
                                  }}
                                />
                              </Box>
                            </Box>

                            {/* Metadata Line */}
                            <Box
                              sx={{ display: 'flex', gap: 2, alignItems: 'center', mt: 0.5, mb: 1 }}
                            >
                              <Typography variant="caption" color="text.secondary">
                                {formatDate(new Date().toISOString())}
                              </Typography>

                              <Divider orientation="vertical" flexItem sx={{ height: 12 }} />

                              <Typography variant="caption" color="text.secondary">
                                {result.metadata.categories || 'General'}
                              </Typography>

                              {result.metadata.pageNum && (
                                <>
                                  <Divider orientation="vertical" flexItem sx={{ height: 12 }} />
                                  <Typography variant="caption" color="text.secondary">
                                    Page {result.metadata.pageNum}
                                  </Typography>
                                </>
                              )}
                              {['xlsx', 'csv', 'xls'].includes(result.metadata.extension) &&
                                result.metadata.blockNum && (
                                  <>
                                    <Divider orientation="vertical" flexItem sx={{ height: 12 }} />
                                    <Typography variant="caption" color="text.secondary">
                                      Row{' '}
                                      {result.metadata?.extension === 'csv'
                                        ? result.metadata.blockNum[0] + 1
                                        : result.metadata.blockNum[0]}
                                    </Typography>
                                  </>
                                )}
                            </Box>

                            {/* Content Preview */}
                            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                              {highlightText(getContentPreview(result.content), searchQuery, theme)}
                            </Typography>

                            {/* Tags and Departments */}
                            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
                              {result.metadata.topics &&
                                result.metadata.topics.slice(0, 3).map((topic) => (
                                  <Chip
                                    key={topic}
                                    label={topic}
                                    size="small"
                                    sx={{
                                      height: 20,
                                      fontSize: '0.7rem',
                                      borderRadius: '4px',
                                    }}
                                  />
                                ))}

                              {result.metadata.departments &&
                                result.metadata.departments.slice(0, 2).map((dept) => (
                                  <Chip
                                    key={dept}
                                    label={dept}
                                    size="small"
                                    variant="outlined"
                                    sx={{
                                      height: 20,
                                      fontSize: '0.7rem',
                                      borderRadius: '4px',
                                    }}
                                  />
                                ))}

                              {((result.metadata.topics?.length || 0) > 3 ||
                                (result.metadata.departments?.length || 0) > 2) && (
                                <Chip
                                  label={`+${(result.metadata.topics?.length || 0) - 3 + ((result.metadata.departments?.length || 0) - 2)} more`}
                                  size="small"
                                  sx={{
                                    height: 20,
                                    fontSize: '0.7rem',
                                    borderRadius: '4px',
                                  }}
                                />
                              )}
                            </Box>
                          </Box>
                        </Box>
                      </CardContent>
                    </Card>
                  );
                })}

                {/* Loading Indicator at Bottom */}
                {loading && searchResults.length > 0 && canLoadMore && (
                  <Box
                    sx={{
                      display: 'flex',
                      justifyContent: 'center',
                      alignItems: 'center',
                      p: 2,
                      gap: 1,
                    }}
                  >
                    <CircularProgress size={16} />
                    <Typography variant="body2" color="text.secondary">
                      Loading more results...
                    </Typography>
                  </Box>
                )}

                {/* End of Results Indicator */}
                {!loading && searchResults.length >= 10 && !canLoadMore && (
                  <Box
                    sx={{
                      display: 'flex',
                      justifyContent: 'center',
                      alignItems: 'center',
                      p: 2,
                    }}
                  >
                    <Typography variant="body2" color="text.secondary">
                      No more results to load
                    </Typography>
                  </Box>
                )}
              </Box>
            )}
          </Box>
        </Box>
      </Box>
    </Box>
  );
};

export default KnowledgeSearch;
