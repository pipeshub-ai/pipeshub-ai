// all-records-page.tsx - Standalone All Records page with hierarchical navigation
import { useCallback, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Box } from '@mui/material';
import { paths } from 'src/routes/paths';
import AllRecordsView from './components/all-records-view';

export default function AllRecordsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  
  const cursor = searchParams.get('cursor') || undefined;
  const nodeType = searchParams.get('nodeType') || undefined;
  const nodeId = searchParams.get('nodeId') || undefined;
  const limit = searchParams.get('limit') || '20';
  const q = searchParams.get('q') || undefined;
  const sortBy = searchParams.get('sortBy') || undefined;
  const sortOrder = searchParams.get('sortOrder') || undefined;
  const recordTypes = searchParams.get('recordTypes') || undefined;
  const origins = searchParams.get('origins') || undefined;
  const connectorIds = searchParams.get('connectorIds') || undefined;
  const kbIds = searchParams.get('kbIds') || undefined;
  const indexingStatus = searchParams.get('indexingStatus') || undefined;
  
  const updateUrl = useCallback((params: Record<string, string | undefined>) => {
    const newParams = new URLSearchParams(searchParams);
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') {
        newParams.delete(key);
      } else {
        newParams.set(key, value);
      }
    });
    navigate(`${paths.dashboard.allRecords}?${newParams.toString()}`);
  }, [navigate, searchParams]);

  const CURSOR_ONLY_EXTRA_KEYS = [
    'q',
    'sortBy',
    'sortOrder',
    'recordTypes',
    'origins',
    'connectorIds',
    'kbIds',
    'indexingStatus',
    'limit',
  ] as const;

  useEffect(() => {
    if (!cursor) return;
    if (!CURSOR_ONLY_EXTRA_KEYS.some((k) => searchParams.has(k))) return;

    const newParams = new URLSearchParams();
    newParams.set('cursor', cursor);
    const nt = searchParams.get('nodeType');
    const nid = searchParams.get('nodeId');
    if (nt) newParams.set('nodeType', nt);
    if (nid) newParams.set('nodeId', nid);
    navigate(`${paths.dashboard.allRecords}?${newParams.toString()}`, { replace: true });
  }, [cursor, navigate, searchParams]);

  const handleNavigateToRecord = (recordId: string) => {
    window.open(`/record/${recordId}`, '_blank');
  };
  
  return (
    <Box>
      <AllRecordsView
        cursor={cursor}
        nodeType={nodeType}
        nodeId={nodeId}
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
          sortOrder,
        }}
        onUpdateUrl={updateUrl}
        onNavigateToRecord={handleNavigateToRecord}
      />
    </Box>
  );
}

