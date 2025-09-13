import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Box,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Chip,
  OutlinedInput,
  Checkbox,
  ListItemText,
  Stack,
  Divider,
  useTheme,
  alpha,
} from '@mui/material';
import { Iconify } from 'src/components/iconify';
import filterIcon from '@iconify-icons/mdi/filter';
import checkIcon from '@iconify-icons/mdi/check';
import closeIcon from '@iconify-icons/mdi/close';
import { Connector } from '../types/types';
import { ConnectorApiService } from '../services/api';

interface FilterSelectionDialogProps {
  connector: Connector;
  filterOptions: any;
  onClose: () => void;
  onSave: (filters: any) => void;
  isEnabling?: boolean;
}

const FilterSelectionDialog: React.FC<FilterSelectionDialogProps> = ({
  connector,
  filterOptions,
  onClose,
  onSave,
  isEnabling = false,
}) => {
  const theme = useTheme();
  const [selectedFilters, setSelectedFilters] = useState<any>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Initialize with empty selections
    const initialFilters: any = {};
    
    if (filterOptions.folders) {
      initialFilters.folders = [];
    }
    if (filterOptions.fileTypes) {
      initialFilters.fileTypes = [];
    }
    if (filterOptions.labels) {
      initialFilters.labels = [];
    }
    if (filterOptions.channels) {
      initialFilters.channels = [];
    }
    
    setSelectedFilters(initialFilters);
  }, [filterOptions]);

  const handleFilterChange = (filterType: string, value: any) => {
    setSelectedFilters((prev: any) => ({
      ...prev,
      [filterType]: value,
    }));
  };

  const handleSave = async () => {
    try {
      setSaving(true);
      setError(null);
      
      // Save the selected filters to the backend
      await ConnectorApiService.saveConnectorFilters(connector.name, selectedFilters);
      
      // Call the onSave callback
      onSave(selectedFilters);
      
      onClose();
    } catch (saveError) {
      console.error('Error saving filters:', saveError);
      setError('Failed to save filters. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const renderFolderSelector = () => {
    if (!filterOptions.folders || filterOptions.folders.length === 0) return null;

    return (
      <FormControl fullWidth sx={{ mb: 2 }}>
        <InputLabel>Select Folders</InputLabel>
        <Select
          multiple
          value={selectedFilters.folders || []}
          onChange={(e) => handleFilterChange('folders', e.target.value)}
          input={<OutlinedInput label="Select Folders" />}
          renderValue={(selected) => (
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
              {selected.map((value: string) => {
                const folder = filterOptions.folders.find((f: any) => f.id === value);
                return (
                  <Chip
                    key={value}
                    label={folder?.name || value}
                    size="small"
                    sx={{
                      backgroundColor: alpha(theme.palette.primary.main, 0.1),
                      color: theme.palette.primary.main,
                    }}
                  />
                );
              })}
            </Box>
          )}
        >
          {filterOptions.folders.map((folder: any) => (
            <MenuItem key={folder.id} value={folder.id}>
              <Checkbox checked={(selectedFilters.folders || []).indexOf(folder.id) > -1} />
              <ListItemText primary={folder.name} />
            </MenuItem>
          ))}
        </Select>
      </FormControl>
    );
  };

  const renderFileTypeSelector = () => {
    if (!filterOptions.fileTypes || filterOptions.fileTypes.length === 0) return null;

    return (
      <FormControl fullWidth sx={{ mb: 2 }}>
        <InputLabel>Select File Types</InputLabel>
        <Select
          multiple
          value={selectedFilters.fileTypes || []}
          onChange={(e) => handleFilterChange('fileTypes', e.target.value)}
          input={<OutlinedInput label="Select File Types" />}
          renderValue={(selected) => (
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
              {selected.map((value: string) => {
                const fileType = filterOptions.fileTypes.find((ft: any) => ft.value === value);
                return (
                  <Chip
                    key={value}
                    label={fileType?.label || value}
                    size="small"
                    sx={{
                      backgroundColor: alpha(theme.palette.secondary.main, 0.1),
                      color: theme.palette.secondary.main,
                    }}
                  />
                );
              })}
            </Box>
          )}
        >
          {filterOptions.fileTypes.map((fileType: any) => (
            <MenuItem key={fileType.value} value={fileType.value}>
              <Checkbox checked={(selectedFilters.fileTypes || []).indexOf(fileType.value) > -1} />
              <ListItemText primary={fileType.label} />
            </MenuItem>
          ))}
        </Select>
      </FormControl>
    );
  };

  const renderLabelSelector = () => {
    if (!filterOptions.labels || filterOptions.labels.length === 0) return null;

    return (
      <FormControl fullWidth sx={{ mb: 2 }}>
        <InputLabel>Select {connector.name} Labels</InputLabel>
        <Select
          multiple
          value={selectedFilters.labels || []}
          onChange={(e) => handleFilterChange('labels', e.target.value)}
          input={<OutlinedInput label={`Select ${connector.name} Labels`} />}
          renderValue={(selected) => (
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
              {selected.map((value: string) => {
                const label = filterOptions.labels.find((l: any) => l.id === value);
                return (
                  <Chip
                    key={value}
                    label={label?.name || value}
                    size="small"
                    sx={{
                      backgroundColor: alpha(theme.palette.info.main, 0.1),
                      color: theme.palette.info.main,
                    }}
                  />
                );
              })}
            </Box>
          )}
        >
          {filterOptions.labels.map((label: any) => (
            <MenuItem key={label.id} value={label.id}>
              <Checkbox checked={(selectedFilters.labels || []).indexOf(label.id) > -1} />
              <ListItemText primary={label.name} />
            </MenuItem>
          ))}
        </Select>
      </FormControl>
    );
  };

  const renderChannelSelector = () => {
    if (!filterOptions.channels || filterOptions.channels.length === 0) return null;

    return (
      <FormControl fullWidth sx={{ mb: 2 }}>
        <InputLabel>Select {connector.name} Channels</InputLabel>
        <Select
          multiple
          value={selectedFilters.channels || []}
          onChange={(e) => handleFilterChange('channels', e.target.value)}
          input={<OutlinedInput label={`Select ${connector.name} Channels`} />}
          renderValue={(selected) => (
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5 }}>
              {selected.map((value: string) => {
                const channel = filterOptions.channels.find((c: any) => c.id === value);
                return (
                  <Chip
                    key={value}
                    label={channel?.name || value}
                    size="small"
                    sx={{
                      backgroundColor: alpha(theme.palette.warning.main, 0.1),
                      color: theme.palette.warning.main,
                    }}
                  />
                );
              })}
            </Box>
          )}
        >
          {filterOptions.channels.map((channel: any) => (
            <MenuItem key={channel.id} value={channel.id}>
              <Checkbox checked={(selectedFilters.channels || []).indexOf(channel.id) > -1} />
              <ListItemText primary={channel.name} />
            </MenuItem>
          ))}
        </Select>
      </FormControl>
    );
  };

  const getDialogTitle = () => {
    const baseTitle = `Select ${connector.name} Filters`;
    return isEnabling ? `Enable ${connector.name} - ${baseTitle}` : baseTitle;
  };

  return (
    <Dialog
      open
      onClose={onClose}
      maxWidth="md"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: 2,
          minHeight: '400px',
        },
      }}
    >
      <DialogTitle>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Iconify icon={filterIcon} width={24} height={24} />
          <Typography variant="h6" sx={{ fontWeight: 600 }}>
            {getDialogTitle()}
          </Typography>
        </Stack>
      </DialogTitle>

      <Divider />

      <DialogContent sx={{ py: 3 }}>
        <Stack spacing={2}>
          {error && (
            <Typography color="error" variant="body2" sx={{ mb: 2 }}>
              {error}
            </Typography>
          )}
          {renderFolderSelector()}
          {renderFileTypeSelector()}
          {renderLabelSelector()}
          {renderChannelSelector()}
        </Stack>
      </DialogContent>

      <Divider />

      <DialogActions sx={{ p: 2 }}>
        <Button
          onClick={onClose}
          startIcon={<Iconify icon={closeIcon} width={16} height={16} />}
          sx={{ textTransform: 'none' }}
        >
          Cancel
        </Button>
        <Button
          onClick={handleSave}
          variant="contained"
          disabled={saving}
          startIcon={<Iconify icon={checkIcon} width={16} height={16} />}
          sx={{ textTransform: 'none' }}
        >
          {saving ? (isEnabling ? 'Enabling...' : 'Saving...') : (isEnabling ? 'Enable & Save Filters' : 'Save Filters')}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default FilterSelectionDialog;
