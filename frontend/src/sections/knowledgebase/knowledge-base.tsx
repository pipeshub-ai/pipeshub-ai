import debounce from 'lodash/debounce';
import { useRef, useMemo, useState, useEffect, useCallback } from 'react';

import { 
  Box, 
  Fade, 
  alpha, 
  useTheme,
  LinearProgress,
  CircularProgress
} from '@mui/material';

import { fetchKnowledgeBaseDetails } from './utils';
import KnowledgeBaseSideBar from './knowledge-base-sidebar';
import KnowledgeBaseDetails from './knowledge-base-details';

import type { Filters, KnowledgeBaseResponse } from './types/knowledge-base';

// Constants for sidebar widths
const SIDEBAR_EXPANDED_WIDTH = 320;
const SIDEBAR_COLLAPSED_WIDTH = 64;

// Create a function to initialize empty filters
const createEmptyFilters = (): Filters => ({
  indexingStatus: [],
  department: [],
  moduleId: [],
  searchTags: [],
  appSpecificRecordType: [],
  recordTypes: [],
  origin: [],
  status: [],
  connectors: [],
  app: [],
  permissions: [],
});

export default function KnowledgeBase() {
  const theme = useTheme();
  
  // Initialize all filter arrays properly
  const [filters, setFilters] = useState<Filters>(createEmptyFilters());

  const [searchQuery, setSearchQuery] = useState<string>('');
  const [knowledgeBaseData, setKnowledgeBaseData] = useState<KnowledgeBaseResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  // Add a separate loading state for filter changes to prevent flickering
  const [filterLoading, setFilterLoading] = useState<boolean>(false);
  const [pagination, setPagination] = useState({
    page: 1,
    limit: 20,
  });
  const [openSidebar, setOpenSidebar] = useState<boolean>(true);
  
  // Add a ref to track filter changes
  const isFilterChanging = useRef(false);
  // Add a ref to store previous filter state for comparison
  const prevFiltersRef = useRef<Filters>(createEmptyFilters());

  const toggleSidebar = useCallback(() => {
    setOpenSidebar((prev) => !prev);
  }, []);

  // This function directly handles the API call with the provided filters
  const handleFetchData = useCallback(
    (filter: Filters, searchQueryContent: string) => {
      
      // Show different loading indicators based on what changed
      if (JSON.stringify(prevFiltersRef.current) !== JSON.stringify(filter)) {
        setFilterLoading(true);
      } else {
        setLoading(true);
      }
      
      // Update previous filters ref
      prevFiltersRef.current = filter;
      
      try {
        const queryParams = new URLSearchParams();

        // Process each filter - convert arrays to comma-separated strings
        Object.entries(filter).forEach(([key, values]) => {
          if (Array.isArray(values) && values.length > 0) {
            // Key mapping - some keys need to be renamed to match backend schema
            const paramKey = key === 'origin' ? 'origins' : key;
            
            // Join array values with commas
            const valueString = values.join(',');
            
            // Add as a single parameter
            queryParams.append(paramKey, valueString);
          }
        });

        // Add pagination and search parameters
        if (searchQueryContent) {
          queryParams.append('search', searchQueryContent);
        }
        queryParams.append('page', pagination.page.toString());
        queryParams.append('limit', pagination.limit.toString());

        // Log the final URL for debugging
        const url = `/api/v1/knowledgebase?${queryParams.toString()}`;

        fetchKnowledgeBaseDetails(queryParams)
          .then((data) => {
            setKnowledgeBaseData(data);
          })
          .catch((error) => {
            console.error('Error fetching knowledge base details:', error);
          })
          .finally(() => {
            setTimeout(() => {
              setLoading(false);
              setFilterLoading(false);
            }, 100);
          });
      } catch (error) {
        console.error('Error preparing request:', error);
        setLoading(false);
        setFilterLoading(false);
      }
    },
    [pagination.page, pagination.limit]
  );

  // Create the debounced version of the fetch function
  const debouncedFetch = useMemo(
    () => debounce((filter: Filters, query: string) => {
      handleFetchData(filter, query);
    }, 400),
    [handleFetchData]
  );

  // Effect to trigger fetch when filters or pagination changes
  useEffect(() => {
    debouncedFetch(filters, searchQuery);
    
    return () => {
      debouncedFetch.cancel();
    };
  }, [filters, searchQuery, pagination, debouncedFetch]);

  // Handler for filter changes from sidebar
  const handleFilterChange = (newFilters: Filters): void => {
    // If a filter operation is already in progress, return
    if (isFilterChanging.current) return;
    
    isFilterChanging.current = true;
    
    // Create a base empty filters object
    const normalizedFilters = { ...createEmptyFilters() };
    
    // Copy values from newFilters, ensuring all are arrays
    Object.keys(normalizedFilters).forEach((key) => {
      const filterKey = key as keyof Filters;
      
      // If the property exists in newFilters and is an array, use it
      // Otherwise use an empty array
      if (newFilters[filterKey] !== undefined && Array.isArray(newFilters[filterKey])) {
        normalizedFilters[filterKey] = [...newFilters[filterKey]!];
      }
    });
    
    
    // Use requestAnimationFrame to batch UI updates
    requestAnimationFrame(() => {
      // Update the filters state
      setFilters(normalizedFilters);
      // Reset to first page on filter change
      setPagination((prev) => ({ ...prev, page: 1 }));
      
      // Reset the flag after a short delay
      setTimeout(() => {
        isFilterChanging.current = false;
      }, 50);
    });
  };

  const handleSearchChange = (query: string): void => {
    setSearchQuery(query);
    setPagination((prev) => ({ ...prev, page: 1 }));
  };

  const handlePageChange = (newPage: number): void => {
    setPagination((prev) => ({ ...prev, page: newPage }));
  };

  const handleLimitChange = (newLimit: number): void => {
    setPagination({ page: 1, limit: newLimit });
  };

  return (
    <Box
      sx={{
        display: 'flex',
        position: 'relative',
        flexGrow: 1,
        overflow: 'hidden',
        zIndex: 0,
        bgcolor: alpha(theme.palette.background.default, 0.4),
      }}
    >
      {/* Sidebar */}
      <KnowledgeBaseSideBar
        sx={{
          zIndex: 100,
          position: 'relative',
        }}
        filters={filters}
        onFilterChange={handleFilterChange}
        openSidebar={openSidebar}
        onToggleSidebar={toggleSidebar}
      />

      {/* Filter Loading Indicator */}
      {filterLoading && (
        <Box
          sx={{
            position: 'absolute',
            top: 0,
            left: openSidebar ? SIDEBAR_EXPANDED_WIDTH : SIDEBAR_COLLAPSED_WIDTH,
            right: 0,
            zIndex: 1000,
          }}
        >
          <LinearProgress 
            color="primary" 
            sx={{ 
              height: 3,
              bgcolor: 'transparent',
              '& .MuiLinearProgress-bar': {
                transition: theme.transitions.create(['transform'], {
                  easing: theme.transitions.easing.easeInOut,
                  duration: '0.3s',
                }),
              }
            }} 
          />
        </Box>
      )}

      {/* Details Section */}
      <Box
        sx={{
          flexGrow: 1,
          overflow: 'auto',
          width: `calc(100% - ${openSidebar ? SIDEBAR_EXPANDED_WIDTH : SIDEBAR_COLLAPSED_WIDTH}px)`,
          transition: theme.transitions.create(['width'], {
            easing: theme.transitions.easing.sharp,
            duration: '0.25s',
          }),
          marginLeft: 0,
          px: 3,
          py: 2.5,
          position: 'relative',
        }}
      >
        {/* Content Loading Overlay */}
        <Fade in={filterLoading} timeout={150}>
          <Box
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: alpha(theme.palette.background.paper, 0.6),
              zIndex: 10,
              backdropFilter: 'blur(1px)',
            }}
          >
            <CircularProgress size={40} />
          </Box>
        </Fade>
        
        <KnowledgeBaseDetails
          knowledgeBaseData={knowledgeBaseData}
          onSearchChange={handleSearchChange}
          loading={loading && !filterLoading}
          pagination={pagination}
          onPageChange={handlePageChange}
          onLimitChange={handleLimitChange}
        />
      </Box>
    </Box>
  );
}