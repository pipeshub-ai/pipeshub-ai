import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  FormControl,
  Select,
  MenuItem,
  Grid,
  Alert,
  Stack,
  alpha,
  useTheme,
} from '@mui/material';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDayjs } from '@mui/x-date-pickers/AdapterDayjs';
import { ScheduledConfig } from '../types/types';

interface ScheduledSyncConfigProps {
  value: ScheduledConfig;
  onChange: (value: ScheduledConfig) => void;
  disabled?: boolean;
}

// Simplified timezone options
const TIMEZONES = [
  { name: 'UTC', displayName: 'UTC' },
  { name: 'America/New_York', displayName: 'Eastern Time' },
  { name: 'America/Chicago', displayName: 'Central Time' },
  { name: 'America/Denver', displayName: 'Mountain Time' },
  { name: 'America/Los_Angeles', displayName: 'Pacific Time' },
  { name: 'Europe/London', displayName: 'GMT' },
  { name: 'Europe/Paris', displayName: 'CET' },
  { name: 'Asia/Tokyo', displayName: 'JST' },
  { name: 'Asia/Shanghai', displayName: 'CST' },
  { name: 'Asia/Kolkata', displayName: 'IST' },
  { name: 'Australia/Sydney', displayName: 'AET' },
];

// Simplified interval options
const INTERVAL_OPTIONS = [
  { value: 5, label: '5 minutes' },
  { value: 15, label: '15 minutes' },
  { value: 30, label: '30 minutes' },
  { value: 60, label: '1 hour' },
  { value: 240, label: '4 hours' },
  { value: 480, label: '8 hours' },
  { value: 720, label: '12 hours' },
  { value: 1440, label: '1 day' },
  { value: 10080, label: '1 week' },
];

const ScheduledSyncConfig: React.FC<ScheduledSyncConfigProps> = ({
  value = {},
  onChange,
  disabled = false,
}) => {
  const theme = useTheme();
  const isDark = theme.palette.mode === 'dark';
  const [localValue, setLocalValue] = useState<ScheduledConfig>({
    intervalMinutes: 60,
    timezone: 'UTC',
    ...value,
  });

  // Initialize from existing data when value changes
  useEffect(() => {
    if (value) {
      setLocalValue({
        intervalMinutes: value.intervalMinutes || 60,
        timezone: value.timezone || 'UTC',
      });
    }
  }, [value]);

  // Propagate interval and timezone to parent when changed
  useEffect(() => {
    const timeoutId = setTimeout(() => {
      onChange({
        intervalMinutes: localValue.intervalMinutes,
        timezone: localValue.timezone,
      });
    }, 300);

    return () => clearTimeout(timeoutId);
  }, [localValue, onChange]);

  const handleFieldChange = (field: string, newValue: any) => {
    setLocalValue((prev: ScheduledConfig) => ({
      ...prev,
      [field]: newValue,
    }));
  };

  return (
    <LocalizationProvider dateAdapter={AdapterDayjs}>
      <Stack spacing={1.5}>
        {/* Sync Interval - Full Width */}
        <Box>
          <Typography
            variant="body2"
            sx={{
              mb: 1,
              fontWeight: 600,
              fontSize: '0.875rem',
              color: theme.palette.text.primary,
            }}
          >
            Sync Interval
          </Typography>
          <FormControl fullWidth size="small">
            <Select
              value={localValue.intervalMinutes}
              onChange={(e) => handleFieldChange('intervalMinutes', e.target.value)}
              disabled={disabled}
              displayEmpty
              sx={{
                borderRadius: 1.25,
                fontSize: '0.875rem',
                fontWeight: 500,
                backgroundColor: alpha(theme.palette.background.paper, 0.8),
                transition: 'all 0.2s',
                '&:hover .MuiOutlinedInput-notchedOutline': {
                  borderColor: alpha(theme.palette.primary.main, 0.3),
                },
                '&.Mui-focused': {
                  backgroundColor: theme.palette.background.paper,
                },
                '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                  borderColor: theme.palette.primary.main,
                  borderWidth: '1px',
                },
              }}
            >
              <MenuItem disabled value="">
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.875rem' }}>
                  Select interval
                </Typography>
              </MenuItem>
              {INTERVAL_OPTIONS.map((option) => (
                <MenuItem key={option.value} value={option.value}>
                  <Typography variant="body2" sx={{ fontSize: '0.875rem' }}>
                    {option.label}
                  </Typography>
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Box>

        {/* Timezone - Hidden for now */}
        {/* <Grid item xs={12} md={6}>
          <Typography
            variant="body2"
            sx={{
              mb: 1,
              fontWeight: 600,
              fontSize: '0.875rem',
              color: theme.palette.text.primary,
            }}
          >
            Timezone
          </Typography>
          <FormControl fullWidth size="small">
            <Select
              value={localValue.timezone}
              onChange={(e) => handleFieldChange('timezone', e.target.value)}
              disabled={disabled}
              displayEmpty
              sx={{
                borderRadius: 1.25,
                fontSize: '0.875rem',
                fontWeight: 500,
                backgroundColor: alpha(theme.palette.background.paper, 0.8),
                transition: 'all 0.2s',
                '&:hover .MuiOutlinedInput-notchedOutline': {
                  borderColor: alpha(theme.palette.primary.main, 0.3),
                },
                '&.Mui-focused': {
                  backgroundColor: theme.palette.background.paper,
                },
                '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                  borderColor: theme.palette.primary.main,
                  borderWidth: '1px',
                },
              }}
            >
              <MenuItem disabled value="">
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.875rem' }}>
                  Select timezone
                </Typography>
              </MenuItem>
              {TIMEZONES.map((tz) => (
                <MenuItem key={tz.name} value={tz.name}>
                  <Typography variant="body2" sx={{ fontSize: '0.875rem' }}>
                    {tz.displayName}
                  </Typography>
                </MenuItem>
              ))}
            </Select>
          </FormControl>
        </Grid> */}

        {/* Summary */}
        <Box
          sx={{
            p: 1.25,
            borderRadius: 1.25,
            border: `1px solid ${alpha(theme.palette.divider, isDark ? 0.12 : 0.12)}`,
            bgcolor: isDark
              ? alpha(theme.palette.primary.main, 0.08)
              : alpha(theme.palette.primary.main, 0.02),
            transition: 'all 0.2s',
          }}
        >
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{
              fontSize: '0.8125rem',
              lineHeight: 1.5,
              fontWeight: 500,
            }}
          >
            Syncs every{' '}
            {INTERVAL_OPTIONS.find((opt) => opt.value === localValue.intervalMinutes)?.label ||
              '1 hour'}
          </Typography>
        </Box>

        {/* Information Alert */}
        <Alert
          severity="info"
          variant="outlined"
          sx={{
            borderRadius: 1.25,
            py: 0.875,
            px: 1.5,
            '& .MuiAlert-icon': { fontSize: '1.125rem', py: 0.375 },
            '& .MuiAlert-message': { py: 0.125 },
            alignItems: 'center',
          }}
        >
          <Typography variant="body2" sx={{ fontSize: '0.8125rem', lineHeight: 1.5 }}>
            Scheduled syncs will run automatically at the specified intervals.
          </Typography>
        </Alert>
      </Stack>
    </LocalizationProvider>
  );
};

export default ScheduledSyncConfig;
