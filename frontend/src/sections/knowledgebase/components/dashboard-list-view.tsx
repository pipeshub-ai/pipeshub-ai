import React, { memo, useMemo } from 'react';
import { DataGrid, GridColDef } from '@mui/x-data-grid';
import {
  Box,
  Typography,
  Chip,
  Paper,
  Skeleton,
  ListItem,
  ListItemIcon,
  ListItemText,
  ListItemSecondaryAction,
  Divider,
  TablePagination,
} from '@mui/material';
import { alpha } from '@mui/material/styles';
import { Icon } from '@iconify/react';
import { getKBIcon } from '../utils/kb-icon';
import { MenuButton } from './menu-button';
import { KnowledgeBase } from '../types/kb';

const ListViewComponent = memo<{
  knowledgeBases: KnowledgeBase[];
  navigateToKB: (kb: KnowledgeBase) => void;
  onEdit: (kb: KnowledgeBase) => void;
  onDelete: (kb: KnowledgeBase) => void;
  theme: any;
  totalCount: number;
  page: number;
  rowsPerPage: number;
  onPageChange: (page: number) => void;
  onRowsPerPageChange: (rowsPerPage: number) => void;
  loading: boolean;
}>(
  ({
    knowledgeBases,
    navigateToKB,
    onEdit,
    onDelete,
    theme,
    totalCount,
    page,
    rowsPerPage,
    onPageChange,
    onRowsPerPageChange,
    loading,
  }) => {

    const columns = useMemo(() => {
      const baseColumns: GridColDef[] = [
        {
          field: 'name',
          headerName: 'Name',
          flex: 1,
          minWidth: 250,
          renderCell: (params) => {
            const kb = params.row;
            const kbIcon = getKBIcon(kb.name);
            return (
              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  height: '100%',
                  width: '100%',
                  pl: 2.5,
                }}
              >
                <Box
                  sx={{
                    width: 36,
                    height: 36,
                    borderRadius: 1.5,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    backgroundColor: (themeVal) => alpha(themeVal.palette.grey[500], 0.1),
                    color: 'text.secondary',
                    mr: 2,
                    flexShrink: 0,
                  }}
                >
                  <Icon icon={kbIcon} fontSize={18} />
                </Box>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {kb.name}
                </Typography>
              </Box>
            );
          },
        },
        {
          field: 'userRole',
          headerName: 'Role',
          width: 180,
          align: 'center',
          headerAlign: 'center',
          renderCell: (params) => (
            <Chip
              label={params.row.userRole}
              size="small"
              variant="outlined"
              sx={{
                fontSize: '0.7rem',
                height: 24,
                borderRadius: 1,
                ml: 2,
                borderColor: 'divider',
                color: 'text.secondary',
              }}
            />
          ),
        },
        {
          field: 'updatedAtTimestamp',
          headerName: 'Last Updated',
          width: 180,
          align: 'left',
          headerAlign: 'left',
          renderCell: (params) => {
            const date = new Date(params.row.updatedAtTimestamp);
            return (
              <Box sx={{ pl: 2.5, mt: 2 }}>
                <Typography
                  variant="caption"
                  display="block"
                  color="text.primary"
                  sx={{ fontWeight: 500 }}
                >
                  {date.toLocaleDateString()}
                </Typography>
                <Typography variant="caption" display="block" color="text.secondary">
                  {date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </Typography>
              </Box>
            );
          },
        },
        {
          field: 'createdAtTimestamp',
          headerName: 'Created',
          width: 180,
          align: 'left',
          headerAlign: 'left',
          renderCell: (params) => {
            const date = new Date(params.row.createdAtTimestamp);
            return (
              <Box sx={{ pl: 1.5, mt: 2 }}>
                <Typography
                  variant="caption"
                  display="block"
                  color="text.primary"
                  sx={{ fontWeight: 500 }}
                >
                  {date.toLocaleDateString()}
                </Typography>
                <Typography variant="caption" display="block" color="text.secondary">
                  {date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </Typography>
              </Box>
            );
          },
        },
      ];


      const shouldShowActions = knowledgeBases.some((kb) => kb.userRole !== 'READER');


      if (shouldShowActions) {
        baseColumns.push({
          field: 'actions',
          headerName: 'Actions',
          width: 100,
          sortable: false,
          align: 'center',
          headerAlign: 'center',
          renderCell: (params) => {

            if (params.row.userRole === 'READER') {
              return null;
            }
            return <MenuButton kb={params.row} onEdit={onEdit} onDelete={onDelete} theme={theme} />;
          },
        });
      }

      return baseColumns;
    }, [knowledgeBases, onEdit, onDelete, theme]);

    const handleRowClick = (params: any) => {
      navigateToKB(params.row);
    };

    if (loading && knowledgeBases.length === 0) {
      return (
        <Paper
          elevation={0}
          sx={{
            height: 600,
            display: 'flex',
            flexDirection: 'column',
            borderRadius: 2,
            border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
            overflow: 'hidden',
          }}
        >
          <Box sx={{ flexGrow: 1 }}>
            {Array.from(new Array(8)).map((_, index) => (
              <React.Fragment key={index}>
                <ListItem sx={{ py: 2, px: 3 }}>
                  <ListItemIcon>
                    <Skeleton variant="circular" width={40} height={40} />
                  </ListItemIcon>
                  <ListItemText
                    primary={<Skeleton variant="text" width="40%" height={24} />}
                    secondary={<Skeleton variant="text" width="60%" height={20} />}
                  />
                  <ListItemSecondaryAction>
                    <Skeleton variant="text" width={80} height={20} />
                  </ListItemSecondaryAction>
                </ListItem>
                {index < 7 && <Divider />}
              </React.Fragment>
            ))}
          </Box>
        </Paper>
      );
    }

    return (
      <Paper
        elevation={0}
        sx={{
          overflow: 'hidden',
          height: 'calc(100vh - 250px)',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 2,
          border: (themeVal) => `1px solid ${themeVal.palette.divider}`,
        }}
      >
        <Box sx={{ flexGrow: 1, height: 'calc(100% - 64px)' }}>
          <DataGrid
            rows={knowledgeBases}
            columns={columns}
            hideFooterPagination
            disableRowSelectionOnClick
            onRowClick={handleRowClick}
            getRowId={(row) => row.id}
            rowHeight={64}
            localeText={{
              noRowsLabel: 'No knowledge bases found',
            }}
            sx={{
              border: 'none',
              height: '100%',
              '& .MuiDataGrid-columnHeaders': {
                backgroundColor: (themeVal) => alpha(themeVal.palette.background.default, 0.5),
                minHeight: '56px !important',
                height: '56px !important',
                maxHeight: '56px !important',
                lineHeight: '56px !important',
              },
              '& .MuiDataGrid-columnHeader': {
                height: '56px !important',
                maxHeight: '56px !important',
                lineHeight: '56px !important',
                paddingLeft: 2,
              },
              '& .MuiDataGrid-columnHeaderTitle': {
                fontWeight: 600,
                fontSize: '0.875rem',
                color: 'text.primary',
              },
              '& .MuiDataGrid-cell': {
                border: 'none',
                paddingLeft: 1,
                maxHeight: '64px !important',
                minHeight: '64px !important',
                height: '64px !important',
                lineHeight: '64px !important',
              },
              '& .MuiDataGrid-cellContent': {
                maxHeight: '64px !important',
                height: '64px !important',
                lineHeight: '64px !important',
              },
              '& .MuiDataGrid-row': {
                maxHeight: '64px !important',
                minHeight: '64px !important',
                height: '64px !important',
                '&:hover': {
                  backgroundColor: 'action.hover',
                  cursor: 'pointer',
                },
              },
              '& .MuiDataGrid-cell:focus, .MuiDataGrid-cell:focus-within': {
                outline: 'none',
              },
              '& .MuiDataGrid-columnHeader:focus, .MuiDataGrid-columnHeader:focus-within': {
                outline: 'none',
              },
            }}
          />
        </Box>

        {/* Pagination footer */}
        <Box
          sx={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            px: 3,
            py: 2,
            borderTop: (themeVal) => `1px solid ${themeVal.palette.divider}`,
            backgroundColor: (themeVal) => alpha(themeVal.palette.background.default, 0.5),
            height: '64px',
          }}
        >
          <Typography variant="body2" color="text.secondary">
            {totalCount === 0
              ? 'No knowledge bases found'
              : `Showing ${page * rowsPerPage + 1}-${Math.min((page + 1) * rowsPerPage, totalCount)} of ${totalCount} knowledge bases`}
          </Typography>

          <TablePagination
            component="div"
            count={totalCount}
            page={page}
            onPageChange={(event, newPage) => onPageChange(newPage)}
            rowsPerPage={rowsPerPage}
            onRowsPerPageChange={(event) => onRowsPerPageChange(parseInt(event.target.value, 10))}
            rowsPerPageOptions={[10, 20, 50, 100]}
            labelRowsPerPage="Items per page:"
            labelDisplayedRows={({ from, to, count }) =>
              `${from}–${to} of ${count !== -1 ? count : `more than ${to}`}`
            }
            sx={{
              '& .MuiTablePagination-toolbar': {
                px: 0,
                py: 0,
                minHeight: 'auto',
              },
              '& .MuiTablePagination-selectLabel, & .MuiTablePagination-displayedRows': {
                fontSize: '0.875rem',
                color: 'text.secondary',
                fontWeight: 500,
                margin: 0,
              },
              '& .MuiTablePagination-select': {
                fontSize: '0.875rem',
                fontWeight: 500,
                borderRadius: 1,
              },
              '& .MuiTablePagination-actions': {
                '& .MuiIconButton-root': {
                  borderRadius: 1,
                  width: 32,
                  height: 32,
                  '&:hover': {
                    backgroundColor: 'action.hover',
                  },
                },
              },
            }}
          />
        </Box>
      </Paper>
    );
  }
);

ListViewComponent.displayName = 'ListViewComponent';

export { ListViewComponent };

const ListView = memo<{
  filteredKnowledgeBases: KnowledgeBase[];
  navigateToKB: (kb: KnowledgeBase) => void;
  onEditKB: (kb: KnowledgeBase) => void;
  onDeleteKB: (kb: KnowledgeBase) => void;
  theme: any;
  totalCount: number;
  page: number;
  rowsPerPage: number;
  handlePageChange: (page: number) => void;
  handleRowsPerPageChange: (rowsPerPage: number) => void;
  loading: boolean;
}>(
  ({
    filteredKnowledgeBases,
    navigateToKB,
    onEditKB,
    onDeleteKB,
    theme,
    totalCount,
    page,
    rowsPerPage,
    handlePageChange,
    handleRowsPerPageChange,
    loading,
  }) => (
    <ListViewComponent
      knowledgeBases={filteredKnowledgeBases}
      navigateToKB={navigateToKB}
      onEdit={onEditKB}
      onDelete={onDeleteKB}
      theme={theme}
      totalCount={totalCount}
      page={page}
      rowsPerPage={rowsPerPage}
      onPageChange={handlePageChange}
      onRowsPerPageChange={handleRowsPerPageChange}
      loading={loading}
    />
  )
);

ListView.displayName = 'ListView';

export { ListView };
