import React, { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Typography,
  TextField,
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
import dayjs from 'dayjs';
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';
import { MobileDateTimePicker } from '@mui/x-date-pickers/MobileDateTimePicker';
import { ScheduledConfig } from '../types/types';

// Extend dayjs with timezone support
dayjs.extend(utc);
dayjs.extend(timezone);

interface ScheduledSyncConfigProps {
  value: ScheduledConfig;
  onChange: (value: ScheduledConfig) => void;
  error?: string;
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
  error,
  disabled = false,
}) => {
  const theme = useTheme();
  const [localValue, setLocalValue] = useState<ScheduledConfig>({
    intervalMinutes: 60,
    timezone: 'UTC',
    startTime: 0,
    maxRepetitions: undefined,
    ...value,
  });

  // Initialize from existing data when value changes
  useEffect(() => {
    if (value) {
      let startTime = value.startTime || 0;

      // Convert seconds to milliseconds if needed
      if (startTime > 0 && startTime < 1e10) {
        startTime *= 1000;
      }

      const newLocalValue = {
        intervalMinutes: value.intervalMinutes || 60,
        timezone: value.timezone || 'UTC',
        startTime,
        maxRepetitions: value.maxRepetitions ?? undefined,
        nextTime: value.nextTime || 0,
        endTime: value.endTime || 0,
        repetitionCount: value.repetitionCount || 0,
      };

      setLocalValue(newLocalValue);
    }
  }, [value]);

  // Helper function to convert epoch time to dayjs object
  const epochToDayjs = (epochMs: number) => {
    if (!epochMs || epochMs === 0) return null;
    return dayjs(epochMs).tz(localValue.timezone || 'UTC');
  };

  // Helper function to convert dayjs object to epoch time
  const dayjsToEpoch = (dayjsObj: dayjs.Dayjs | null): number => {
    if (!dayjsObj) return 0;
    return dayjsObj.valueOf(); // Returns milliseconds
  };

  // Calculate time values based on user input
  const calculateTimes = useCallback((config: ScheduledConfig) => {
    if (!config.startTime || config.startTime === 0) {
      return null;
    }

    const startTimeMs = config.startTime;
    const nextTimeMs = startTimeMs;
    const endTimeMs = startTimeMs;
    const totalRepetitions = 1;

    return {
      startTime: startTimeMs,
      nextTime: nextTimeMs || 0,
      endTime: endTimeMs,
      totalRepetitions,
    };
  }, []);

  // Update time calculation when config changes
  useEffect(() => {
    // Debounce calculation
    const timeoutId = setTimeout(() => {
      const calculation = calculateTimes(localValue);

      if (calculation) {
        const updatedValue = {
          ...localValue,
          startTime: calculation.startTime,
          nextTime: calculation.nextTime,
          endTime: calculation.endTime,
          // do not override maxRepetitions here; leave as user provided (undefined means not set)
          repetitionCount: 0,
        };

        onChange(updatedValue);
      }
    }, 300);

    return () => clearTimeout(timeoutId);
  }, [localValue, calculateTimes, onChange]);

  const handleFieldChange = (field: string, newValue: any) => {
    setLocalValue((prev: ScheduledConfig) => ({
      ...prev,
      [field]: newValue,
    }));
  };

  return (
    <LocalizationProvider dateAdapter={AdapterDayjs}>
      <Stack spacing={2}>
        <Grid container spacing={2}>
          {/* Start Date & Time */}
          <Grid item xs={12} md={6}>
            <Typography
              variant="body2"
              sx={{
                mb: 1,
                fontWeight: 500,
                fontSize: '0.8125rem',
                color: theme.palette.text.primary,
              }}
            >
              Start Date & Time
            </Typography>
            <MobileDateTimePicker
              orientation="landscape"
              value={epochToDayjs(localValue.startTime || 0)}
              onChange={(newValue) => {
                const epochTime = dayjsToEpoch(newValue);
                handleFieldChange('startTime', epochTime);
              }}
              disabled={disabled}
              timezone={localValue.timezone || 'UTC'}
              format="MMM DD, YYYY hh:mm A"
              slotProps={{
                textField: {
                  fullWidth: true,
                  size: 'small',
                  error: !!error,
                  placeholder: 'Select date and time',
                  sx: {
                    '& .MuiOutlinedInput-root': {
                      borderRadius: 1.25,
                      fontSize: '0.8125rem',
                      '&:hover .MuiOutlinedInput-notchedOutline': {
                        borderColor: alpha(theme.palette.primary.main, 0.25),
                      },
                      '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                        borderColor: theme.palette.primary.main,
                        borderWidth: '1px',
                      },
                    },
                  },
                },

                layout: {
                  sx: {
                    '& .MuiPickersLayout-root': {
                      overflow: 'hidden',
                    },
                    '& .MuiPickersLayout-contentWrapper': {
                      position: 'relative',
                    },
                  },
                },
                calendarHeader: {
                  sx: {
                    '& .MuiPickersCalendarHeader-root': {
                      paddingTop: '16px',
                      paddingBottom: '8px',
                      borderBottom: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                      marginBottom: '8px',
                    },
                    '& .MuiPickersCalendarHeader-label': {
                      fontSize: '0.9375rem',
                      fontWeight: 600,
                      color: theme.palette.text.primary,
                    },
                    '& .MuiPickersCalendarHeader-switchViewButton': {
                      fontSize: '0.9375rem',
                      fontWeight: 600,
                    },
                    '& .MuiPickersArrowSwitcher-button': {
                      borderRadius: 1,
                      '&:hover': {
                        bgcolor: alpha(theme.palette.primary.main, 0.08),
                      },
                    },
                  },
                },
                day: {
                  sx: {
                    '&.MuiPickersDay-root': {
                      borderRadius: 1,
                      fontSize: '0.8125rem',
                      '&:hover': {
                        bgcolor: alpha(theme.palette.primary.main, 0.08),
                      },
                      '&.Mui-selected': {
                        bgcolor: theme.palette.primary.main,
                        '&:hover': {
                          bgcolor: theme.palette.primary.dark,
                        },
                      },
                      '&.MuiPickersDay-today': {
                        border: `1px solid ${alpha(theme.palette.primary.main, 0.3)}`,
                        '&:not(.Mui-selected)': {
                          color: theme.palette.primary.main,
                          fontWeight: 500,
                        },
                      },
                    },
                  },
                },

                actionBar: {
                  sx: {
                    '& .MuiPickersActionBar-root': {
                      borderTop: `1px solid ${alpha(theme.palette.divider, 0.08)}`,
                      padding: '12px 16px',
                    },
                    '& .MuiButton-root': {
                      borderRadius: 1,
                      textTransform: 'none',
                      fontWeight: 500,
                      fontSize: '0.8125rem',
                      '&.MuiButton-text': {
                        color: theme.palette.text.secondary,
                        '&:hover': {
                          bgcolor: alpha(theme.palette.text.secondary, 0.08),
                        },
                      },
                      '&.MuiButton-outlined': {
                        borderColor: alpha(theme.palette.divider, 0.15),
                        '&:hover': {
                          borderColor: alpha(theme.palette.primary.main, 0.25),
                          bgcolor: alpha(theme.palette.primary.main, 0.04),
                        },
                      },
                    },
                  },
                },
              }}
            />
          </Grid>

          {/* Timezone */}
          <Grid item xs={12} md={6}>
            <Typography
              variant="body2"
              sx={{
                mb: 1,
                fontWeight: 500,
                fontSize: '0.8125rem',
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
                  fontSize: '0.8125rem',
                  '&:hover .MuiOutlinedInput-notchedOutline': {
                    borderColor: alpha(theme.palette.primary.main, 0.25),
                  },
                  '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                    borderColor: theme.palette.primary.main,
                    borderWidth: '1px',
                  },
                }}
              >
                <MenuItem disabled value="">
                  <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
                    Select timezone
                  </Typography>
                </MenuItem>
                {TIMEZONES.map((tz) => (
                  <MenuItem key={tz.name} value={tz.name}>
                    <Typography variant="body2" sx={{ fontSize: '0.8125rem' }}>
                      {tz.displayName}
                    </Typography>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>

          {/* Sync Interval */}
          <Grid item xs={12} md={6}>
            <Typography
              variant="body2"
              sx={{
                mb: 1,
                fontWeight: 500,
                fontSize: '0.8125rem',
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
                  fontSize: '0.8125rem',
                  '&:hover .MuiOutlinedInput-notchedOutline': {
                    borderColor: alpha(theme.palette.primary.main, 0.25),
                  },
                  '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                    borderColor: theme.palette.primary.main,
                    borderWidth: '1px',
                  },
                }}
              >
                <MenuItem disabled value="">
                  <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8125rem' }}>
                    Select interval
                  </Typography>
                </MenuItem>
                {INTERVAL_OPTIONS.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    <Typography variant="body2" sx={{ fontSize: '0.8125rem' }}>
                      {option.label}
                    </Typography>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>

          {/* Max Repetitions */}
          <Grid item xs={12} md={6}>
            <Typography
              variant="body2"
              sx={{
                mb: 1,
                fontWeight: 500,
                fontSize: '0.8125rem',
                color: theme.palette.text.primary,
              }}
            >
              Max Repetitions
            </Typography>
            <TextField
              fullWidth
              size="small"
              placeholder="Leave empty if not set"
              type="number"
              value={localValue.maxRepetitions ?? ''}
              onChange={(e) => {
                const raw = e.target.value;
                const parsed = raw === '' ? undefined : parseInt(raw, 10);
                handleFieldChange('maxRepetitions', Number.isNaN(parsed as number) ? undefined : (parsed as number | undefined));
              }}
              disabled={disabled}
              inputProps={{ min: 1 }}
              sx={{
                '& .MuiOutlinedInput-root': {
                  borderRadius: 1.25,
                  fontSize: '0.8125rem',
                  '&:hover .MuiOutlinedInput-notchedOutline': {
                    borderColor: alpha(theme.palette.primary.main, 0.25),
                  },
                  '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                    borderColor: theme.palette.primary.main,
                    borderWidth: '1px',
                  },
                },
              }}
            />
          </Grid>
        </Grid>

        {/* Simple Summary */}
        <Box
          sx={{
            p: 1.5,
            borderRadius: 1.25,
            border: `1px solid ${alpha(theme.palette.divider, 0.12)}`,
            bgcolor: alpha(theme.palette.grey[50], 0.3),
          }}
        >
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{
              fontSize: '0.75rem',
              lineHeight: 1.4,
            }}
          >
            {localValue.startTime
              ? `Sync every ${INTERVAL_OPTIONS.find((opt) => opt.value === localValue.intervalMinutes)?.label || '1 hour'} starting ${epochToDayjs(localValue.startTime)?.format('MMM DD, YYYY hh:mm A') || 'when configured'} (${TIMEZONES.find((tz) => tz.name === localValue.timezone)?.displayName || 'UTC'})`
              : 'Configure the start date and time to set up scheduled sync'}
            {localValue.maxRepetitions &&
              localValue.maxRepetitions > 0 &&
              ` for ${localValue.maxRepetitions} executions`}
          </Typography>
        </Box>

        {/* Information Alert */}
        <Alert
          severity="info"
          variant="outlined"
          sx={{
            borderRadius: 1.5,
            py: 1.25,
          }}
        >
          <Typography variant="body2" sx={{ fontSize: '0.8125rem', lineHeight: 1.4 }}>
            Scheduled syncs will run automatically at the specified intervals. All times are
            calculated based on the selected timezone.
          </Typography>
        </Alert>
      </Stack>
    </LocalizationProvider>
  );
};

export default ScheduledSyncConfig;
