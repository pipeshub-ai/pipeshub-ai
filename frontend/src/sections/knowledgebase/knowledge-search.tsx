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

// Helper function to get file icon based on extension

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

  // If connector found, use its iconPath and color
  if (connector && connector.iconPath) {
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

function isDocViewable(extension: string) {
  const viewableExtensions = ['pdf', 'xlsx', 'xls', 'csv', 'docx', 'html', 'txt', 'md','mdx'];
  return viewableExtensions.includes(extension);
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
  const [activeTab, setActiveTab] = useState<number>(0);
  const [selectedRecord, setSelectedRecord] = useState<SearchResult | null>(null);
  const [detailsOpen, setDetailsOpen] = useState<boolean>(false);
  const navigate = useNavigate();
  const observer = useRef<IntersectionObserver | null>(null);
  const [hasSearched, setHasSearched] = useState<boolean>(false);
  const [loadingRecordId, setLoadingRecordId] = useState<string | null>(null);

  // Synchronize searchQuery with parent component's state
  useEffect(() => {
    // Update the input value when the parent's query changes
    if (searchQuery !== searchInputValue) {
      setSearchInputValue(searchQuery);
    }
    // eslint-disable-next-line
  }, [searchQuery]);

  const handleViewCitations = (record: SearchResult, event: React.MouseEvent) => {
    // Prevent event bubbling to card click handler
    event.stopPropagation();

    const recordId = record.metadata?.recordId || '';
    const extension = record.metadata?.extension || '';
    setLoadingRecordId(recordId);

    const isPdf = extension.toLowerCase() === 'pdf';
    const isExcel = ['xlsx', 'xls', 'csv'].includes(extension.toLowerCase());
    const isDocx = ['docx'].includes(extension.toLowerCase());
    const isHtml = ['html'].includes(extension.toLowerCase());
    const isTextFile = ['txt'].includes(extension.toLowerCase());
    const isMarkdown = ['mdx', 'md'].includes(extension.toLowerCase());
    if (isPdf || isExcel || isDocx || isHtml || isTextFile || isMarkdown) {
      if (onViewCitations) {
        onViewCitations(recordId, extension, record).finally(() => {
          // Reset loading state when complete (whether success or error)
          setLoadingRecordId(null);
        });
      }
    }
  };

  const lastResultElementRef = useCallback(
    (node: Element | null) => {
      if (loading) return;
      if (observer.current) observer.current.disconnect();
      observer.current = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && onTopKChange) {
          onTopKChange((prevTopK: number) => prevTopK + 10);
        }
      });
      if (node) observer.current.observe(node);
    },
    [loading, onTopKChange]
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
    if (onSearchQueryChange) {
      onSearchQueryChange('');
    }
  };

  const handleRecordClick = (record: SearchResult): void => {
    const { recordId } = record.metadata;
    const recordMeta = recordsMap[recordId];
    const { webUrl } = recordMeta;
    if (recordMeta.origin === 'UPLOAD' && !webUrl.startsWith('http')) {
      const baseUrl = `${window.location.protocol}//${window.location.host}`;
      const newWebUrl = baseUrl + webUrl;
      window.open(newWebUrl, '_blank');
    } else {
      window.open(webUrl, '_blank'); // Opens in a new tab/window
    }
  };

  // Function to get record details for metadata display
  // const getRecordDetails = (recordId: string): any => {
  //   try {
  //     const record = recordsMap[recordId];
  //     if (!record) return null;

  //     const fullRecord = searchResults?.records?.find((r: Record) => r._id === recordId);
  //     const fileRecord = searchResults?.fileRecords?.find(
  //       (fr: FileRecord) => fr._id === record.metadata?.fileRecordId
  //     );

  //     return {
  //       modules: fullRecord?.modules || [],
  //       searchTags: fullRecord?.searchTags || [],
  //       version: fullRecord?.version,
  //       uploadedBy: fileRecord?.uploadedBy,
  //     };
  //   } catch (error) {
  //     console.error('Error getting record details:', error);
  //     return null;
  //   }
  // };

  // Get the number of records by type
  const getRecordTypeCount = (type: string): number =>
    searchResults.filter((r) => r.metadata?.recordType === type).length;

  // Get categories by record type for the tabs
  const documentCount = getRecordTypeCount('FILE');
  const faqCount = getRecordTypeCount('FAQ');
  const emailCount = getRecordTypeCount('MAIL');

  // Helper function to render uploaded by information
  // const renderUploadedBy = (recordId: string): React.ReactNode => {
  //   const details = getRecordDetails(recordId);
  //   if (!details) return <Typography variant="body2">N/A</Typography>;

  //   const uploadedById = details.uploadedBy;
  //   const user = users.find((u) => u._id === uploadedById);

  //   if (!user) return <Typography variant="body2">N/A</Typography>;

  //   return (
  //     <>
  //       <Avatar sx={{ width: 24, height: 24 }}>{user.fullName.charAt(0)}</Avatar>
  //       <Typography variant="body2">{user.fullName}</Typography>
  //     </>
  //   );
  // };

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
              borderRadius: 1, // More minimalistic border radius (4px)
              border: `1px solid ${theme.palette.divider}`, // Direct use of divider color
              backgroundColor: theme.palette.background.paper,
            }}
          >
            <Box sx={{ display: 'flex', gap: 1 }}>
              {' '}
              {/* Reduced gap for tighter layout */}
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
                    borderRadius: 1, // Match the Paper border radius
                    fontSize: '0.9rem', // Slightly smaller font size
                  },
                  '& .MuiOutlinedInput-notchedOutline': {
                    borderColor: theme.palette.divider, // Consistent border color
                  },
                  '& .MuiInputBase-input': {
                    py: 1.25, // Better vertical padding
                  },
                }}
                InputProps={{
                  startAdornment: (
                    <InputAdornment position="start">
                      <Icon
                        icon={magnifyIcon}
                        style={{
                          color: theme.palette.text.secondary,
                          fontSize: '1.25rem', // Slightly larger for visibility
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
                          padding: '2px', // Smaller padding for better fit
                        }}
                      >
                        <Icon
                          icon={closeIcon}
                          style={{
                            fontSize: '1rem', // Slightly smaller for better fit
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
                  minWidth: '90px', // Slightly narrower
                  borderRadius: 1, // Match the Paper border radius
                  boxShadow: 'none',
                  textTransform: 'none',
                  fontWeight: 500,
                  fontSize: '0.875rem', // Consistent font size
                  py: 0.75, // Better vertical padding
                  px: 2, // Better horizontal padding
                  '&:hover': {
                    boxShadow: 'none',
                    backgroundColor: theme.palette.primary.dark, // Darker on hover
                  },
                  '&:disabled': {
                    opacity: 0.7, // More subtle disabled state
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

                {/* <Box sx={{ display: 'flex', gap: 2 }}>
                  <ActionButton icon={starIcon} label="Popular topics" />
                  <ActionButton icon={historyIcon} label="Recent searches" />
                </Box> */}
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
                  // result.metadata.extension === 'pdf' ||
                  // ['xlsx', 'xls', 'csv'].includes(result.metadata.extension?.toLowerCase() || '');

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
                              // bgcolor: alpha(
                              //   getFileIconColor(result.metadata.extension || ''),
                              //   0.1
                              // ),
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
                                    // Fallback to database icon if image fails to load
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
                                alignItems: 'center', // Center align items vertically
                                width: '100%',
                                gap: 1, // Ensure some gap between elements
                              }}
                            >
                              {/* Record name with ellipsis for overflow */}
                              <Typography
                                variant="subtitle1"
                                fontWeight={500}
                                sx={{
                                  flexGrow: 1, // Allow text to take available space
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap', // Prevent text wrapping
                                  minWidth: 0, // Allow text to shrink below its content size
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
                                  flexShrink: 0, // Prevent shrinking
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
                                      whiteSpace: 'nowrap', // Prevent button text wrapping
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
                {loading && searchResults.length > 0 && (
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
              </Box>
            )}
          </Box>

          {/* Details Column - This would be where DetailPanel is rendered */}
        </Box>
      </Box>
    </Box>
  );
};

export default KnowledgeSearch;
