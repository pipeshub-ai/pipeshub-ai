// all-records-page.tsx - Standalone All Records page with hierarchical navigation
import { useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Box } from '@mui/material';
import AllRecordsView from './components/all-records-view';

export default function AllRecordsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  
  // Parse all URL params
  const nodeType = searchParams.get('nodeType') || undefined;
  const nodeId = searchParams.get('nodeId') || undefined;
  const page = searchParams.get('page') || '1'; // 1-indexed page
  const limit = searchParams.get('limit') || '20';
  const q = searchParams.get('q') || undefined;
  const sortBy = searchParams.get('sortBy') || undefined;
  const sortOrder = searchParams.get('sortOrder') || undefined;
  const recordTypes = searchParams.get('recordTypes') || undefined;
  const origins = searchParams.get('origins') || undefined;
  const connectorIds = searchParams.get('connectorIds') || undefined;
  const kbIds = searchParams.get('kbIds') || undefined;
  const indexingStatus = searchParams.get('indexingStatus') || undefined;
  
  // Navigation helper - updates URL with new params
  const updateUrl = useCallback((params: Record<string, string | undefined>) => {
    const newParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        newParams.set(key, value);
      }
    });
    navigate(`/all-records?${newParams.toString()}`, { replace: true });
  }, [navigate]);
  
  const handleNavigateToRecord = (recordId: string) => {
    window.open(`/record/${recordId}`, '_blank');
  };
  
  return (
    <Box>
      <AllRecordsView
        nodeType={nodeType}
        nodeId={nodeId}
        page={parseInt(page, 10)}
        limit={parseInt(limit, 10)}
        q={q}
        sortBy={sortBy}
        sortOrder={sortOrder as 'asc' | 'desc' | undefined}
        filters={{
          recordTypes: recordTypes?.split(',').filter(Boolean) || [],
          origins: origins?.split(',').filter(Boolean) || [],
          connectorIds: connectorIds?.split(',').filter(Boolean) || [],
          kbIds: kbIds?.split(',').filter(Boolean) || [],
          indexingStatus: indexingStatus?.split(',').filter(Boolean) || [],
          sortBy,
          sortOrder: sortOrder as 'asc' | 'desc' | undefined,
        }}
        onUpdateUrl={updateUrl}
        onNavigateToRecord={handleNavigateToRecord}
      />
    </Box>
  );
}

