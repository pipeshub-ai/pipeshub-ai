import React, { useState, useEffect, useCallback } from 'react';
import {
  Box,
  Typography,
  TextField,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Grid,
  Alert,
  Stack,
  Chip,
  alpha,
  useTheme,
  Card,
  CardContent,
  InputAdornment,
} from '@mui/material';
import { DateTimePicker } from '@mui/x-date-pickers/DateTimePicker';
import { LocalizationProvider } from '@mui/x-date-pickers/LocalizationProvider';
import { AdapterDayjs } from '@mui/x-date-pickers/AdapterDayjs';
import dayjs from 'dayjs';
import timezone from 'dayjs/plugin/timezone';
import utc from 'dayjs/plugin/utc';
import { Iconify } from 'src/components/iconify';
import calendarIcon from '@iconify-icons/mdi/calendar-clock';
import clockIcon from '@iconify-icons/mdi/clock-outline';
import repeatIcon from '@iconify-icons/mdi/repeat';
import checkIcon from '@iconify-icons/mdi/check-circle';
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

// Timezone options with better formatting
const TIMEZONES = [
  { name: 'UTC', displayName: 'UTC', offset: '+00:00' },
  { name: 'America/New_York', displayName: 'Eastern Time', offset: '-05:00' },
  { name: 'America/Chicago', displayName: 'Central Time', offset: '-06:00' },
  { name: 'America/Denver', displayName: 'Mountain Time', offset: '-07:00' },
  { name: 'America/Los_Angeles', displayName: 'Pacific Time', offset: '-08:00' },
  { name: 'Europe/London', displayName: 'GMT', offset: '+00:00' },
  { name: 'Europe/Paris', displayName: 'CET', offset: '+01:00' },
  { name: 'Asia/Tokyo', displayName: 'JST', offset: '+09:00' },
  { name: 'Asia/Shanghai', displayName: 'CST', offset: '+08:00' },
  { name: 'Asia/Kolkata', displayName: 'IST', offset: '+05:30' },
  { name: 'Australia/Sydney', displayName: 'AET', offset: '+10:00' },
];

// Interval options with better descriptions
const INTERVAL_OPTIONS = [
  { value: 5, label: '5 min', description: 'Very frequent', color: '#f44336' },
  { value: 15, label: '15 min', description: 'Frequent', color: '#ff9800' },
  { value: 30, label: '30 min', description: 'Regular', color: '#ffc107' },
  { value: 60, label: '1 hour', description: 'Hourly', color: '#4caf50' },
  { value: 240, label: '4 hours', description: 'Quarterly', color: '#2196f3' },
  { value: 480, label: '8 hours', description: 'Twice daily', color: '#9c27b0' },
  { value: 720, label: '12 hours', description: 'Twice daily', color: '#673ab7' },
  { value: 1440, label: '1 day', description: 'Daily', color: '#3f51b5' },
  { value: 10080, label: '1 week', description: 'Weekly', color: '#607d8b' },
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
    maxRepetitions: 0,
    ...value,
  });

  // Initialize from existing data when value changes
  useEffect(() => {
    if (value) {
      let startTime = value.startTime || 0;
      
      // Convert seconds to milliseconds if needed
      if (startTime > 0 && startTime < 1e10) {
        startTime = startTime * 1000;
      }

      const newLocalValue = {
        intervalMinutes: value.intervalMinutes || 60,
        timezone: value.timezone || 'UTC',
        startTime: startTime,
        maxRepetitions: value.maxRepetitions || 0,
        nextTime: value.nextTime,
        endTime: value.endTime,
        repetitionCount: value.repetitionCount,
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
    const intervalMs = (config.intervalMinutes || 60) * 60 * 1000;
    
    let endTimeMs: number;
    let totalRepetitions: number;

    totalRepetitions = 1;
    endTimeMs = startTimeMs;
    

    const nextTimeMs = startTimeMs;

    return {
      startTime: startTimeMs,
      nextTime: nextTimeMs,
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
          maxRepetitions: calculation.totalRepetitions,
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

  const getSelectedInterval = () => {
    return INTERVAL_OPTIONS.find(opt => opt.value === localValue.intervalMinutes) || INTERVAL_OPTIONS[3];
  };

  const getSelectedTimezone = () => {
    return TIMEZONES.find(tz => tz.name === localValue.timezone) || TIMEZONES[0];
  };

  return (
    <LocalizationProvider dateAdapter={AdapterDayjs}>
      <Box>
        <Stack spacing={2}>
          {/* Configuration Form */}
          <Card elevation={0} sx={{ border: `1px solid ${theme.palette.divider}`, borderRadius: 1.5 }}>
            <CardContent sx={{ p: 3 }}>
              <Grid container spacing={2.5}>
                      {/* Start Date & Time */}
                      <Grid item xs={12} md={6}>
                        <Box>
                          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
                            Start Date & Time
                          </Typography>
                          <DateTimePicker
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
                                helperText: error || 'When the first sync should run',
                                placeholder: 'Select date and time',
                                sx: {
                                  '& .MuiOutlinedInput-root': {
                                    borderRadius: 1,
                                    '&:hover .MuiOutlinedInput-notchedOutline': {
                                      borderColor: theme.palette.primary.main,
                                    },
                                    '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                                      borderColor: theme.palette.primary.main,
                                      borderWidth: 2,
                                    },
                                  },
                                  '& .MuiInputBase-input': {
                                    paddingLeft: 3,
                                    fontSize: '0.875rem',
                                    fontWeight: 500,
                                  },
                                },
                              },
                              popper: {
                                sx: {
                                  '& .MuiPaper-root': {
                                    borderRadius: 2,
                                    boxShadow: `0 12px 40px ${alpha(theme.palette.common.black, 0.15)}`,
                                    border: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
                                    overflow: 'hidden',
                                  },
                                },
                              },
                              actionBar: {
                                sx: {
                                  '& .MuiButton-root': {
                                    borderRadius: 1,
                                    textTransform: 'none',
                                    fontWeight: 500,
                                  },
                                },
                              },
                              calendarHeader: {
                                sx: {
                                  '& .MuiPickersCalendarHeader-root': {
                                        padding: '16px 20px 8px',
                                  },
                                  '& .MuiPickersCalendarHeader-label': {
                                    fontSize: '1rem',
                                    fontWeight: 600,
                                  },
                                },
                              },
                            }}
                            sx={{
                              '& .MuiInputBase-root': {
                                position: 'relative',
                                '&::before': {
                                  content: '""',
                                  position: 'absolute',
                                  left: 8,
                                  top: '50%',
                                  transform: 'translateY(-50%)',
                                  width: 16,
                                  height: 16,
                                  backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%23666' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='4' width='18' height='18' rx='2' ry='2'%3E%3C/rect%3E%3Cline x1='16' y1='2' x2='16' y2='6'%3E%3C/line%3E%3Cline x1='8' y1='2' x2='8' y2='6'%3E%3C/line%3E%3Cline x1='3' y1='10' x2='21' y2='10'%3E%3C/line%3E%3C/svg%3E")`,
                                  backgroundRepeat: 'no-repeat',
                                  backgroundPosition: 'center',
                                  zIndex: 1,
                                  pointerEvents: 'none',
                                },
                              },
                            }}
                          />
                        </Box>
                      </Grid>

                      {/* Timezone */}
                      <Grid item xs={12} md={6}>
                        <Box>
                          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
                            Timezone
                          </Typography>
                          <FormControl fullWidth size="small">
                            <InputLabel sx={{ ml: 3 }}>Select Timezone</InputLabel>
                            <Select
                              value={localValue.timezone}
                              onChange={(e) => handleFieldChange('timezone', e.target.value)}
                              disabled={disabled}
                              label="Select Timezone"
                              sx={{ 
                                borderRadius: 1,
                                '&:hover .MuiOutlinedInput-notchedOutline': {
                                  borderColor: theme.palette.primary.main,
                                },
                                '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                                  borderColor: theme.palette.primary.main,
                                  borderWidth: 2,
                                },
                              }}
                              startAdornment={
                                <InputAdornment position="start" sx={{ ml: 1 }}>
                                  <Iconify icon={clockIcon} width={16} height={16} color={theme.palette.text.secondary} />
                                </InputAdornment>
                              }
                            >
                              {TIMEZONES.map((tz) => (
                                <MenuItem key={tz.name} value={tz.name} sx={{ py: 1.5 }}>
                                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, width: '100%' }}>
                                    <Box
                                      sx={{
                                        width: 6,
                                        height: 6,
                                        borderRadius: '50%',
                                        bgcolor: theme.palette.primary.main,
                                      }}
                                    />
                                    <Typography variant="body2" sx={{ fontWeight: 500, flex: 1 }}>
                                      {tz.displayName}
                                    </Typography>
                                    <Typography variant="caption" color="text.secondary" sx={{ 
                                      px: 1, 
                                      py: 0.5, 
                                      borderRadius: 0.5, 
                                      bgcolor: alpha(theme.palette.grey[500], 0.1),
                                      fontSize: '0.75rem',
                                    }}>
                                      {tz.offset}
                                    </Typography>
                                  </Box>
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                        </Box>
                      </Grid>

                      {/* Sync Interval */}
                      <Grid item xs={12} md={6}>
                        <Box>
                          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
                            Sync Interval
                          </Typography>
                          <FormControl fullWidth size="small">
                            <InputLabel>Select Interval</InputLabel>
                            <Select
                              value={localValue.intervalMinutes}
                              onChange={(e) => handleFieldChange('intervalMinutes', e.target.value)}
                              disabled={disabled}
                              label="Select Interval"
                              sx={{ 
                                borderRadius: 1,
                                '&:hover .MuiOutlinedInput-notchedOutline': {
                                  borderColor: theme.palette.primary.main,
                                },
                                '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                                  borderColor: theme.palette.primary.main,
                                  borderWidth: 2,
                                },
                              }}
                            >
                              {INTERVAL_OPTIONS.map((option) => (
                                <MenuItem key={option.value} value={option.value} sx={{ py: 1.5 }}>
                                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, width: '100%' }}>
                                    <Box
                                      sx={{
                                        width: 10,
                                        height: 10,
                                        borderRadius: '50%',
                                        bgcolor: option.color,
                                        boxShadow: `0 2px 4px ${alpha(option.color, 0.3)}`,
                                      }}
                                    />
                                    <Typography variant="body2" sx={{ fontWeight: 500, flex: 1 }}>
                                      {option.label}
                                    </Typography>
                                    <Typography variant="caption" color="text.secondary" sx={{ 
                                      px: 1, 
                                      py: 0.5, 
                                      borderRadius: 0.5, 
                                      bgcolor: alpha(theme.palette.grey[500], 0.1),
                                      fontSize: '0.75rem',
                                    }}>
                                      {option.description}
                                    </Typography>
                                  </Box>
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                        </Box>
                      </Grid>

                      {/* Max Repetitions */}
                      <Grid item xs={12} md={6}>
                        <Box>
                          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
                            Max Repetitions
                          </Typography>
                          <TextField
                            fullWidth
                            size="small"
                            placeholder="0 = infinite"
                            type="number"
                            value={localValue.maxRepetitions || 0}
                            onChange={(e) => handleFieldChange('maxRepetitions', parseInt(e.target.value) || 0)}
                            disabled={disabled}
                            helperText="0 = infinite repetitions"
                            inputProps={{ min: 0 }}
                            sx={{
                              '& .MuiOutlinedInput-root': {
                                borderRadius: 1,
                                '&:hover .MuiOutlinedInput-notchedOutline': {
                                  borderColor: theme.palette.primary.main,
                                },
                                '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
                                  borderColor: theme.palette.primary.main,
                                  borderWidth: 2,
                                },
                              },
                              '& .MuiInputBase-input': {
                                paddingLeft: 3,
                                fontSize: '0.875rem',
                                fontWeight: 500,
                              },
                            }}
                            InputProps={{
                              startAdornment: (
                                <InputAdornment position="start" sx={{ ml: 1 }}>
                                  <Iconify icon={repeatIcon} width={16} height={16} color={theme.palette.text.secondary} />
                                </InputAdornment>
                              ),
                            }}
                          />
                        </Box>
                      </Grid>
                    </Grid>

                  </CardContent>
                </Card>

                {/* Status Summary */}
                <Box
                  sx={{
                    mt: 2,
                    p: 2,
                    borderRadius: 1.5,
                    border: `1px solid ${alpha(theme.palette.primary.main, 0.2)}`,
                    bgcolor: alpha(theme.palette.primary.main, 0.02),
                  }}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
                    <Iconify icon={checkIcon} width={16} height={16} color={theme.palette.primary.main} />
                    <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                      Schedule Summary
                    </Typography>
                  </Box>
                  <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                    <Chip
                      icon={<Iconify icon={repeatIcon} width={14} height={14} />}
                      label="Recurring"
                      size="small"
                      color="primary"
                      variant="outlined"
                    />
                    <Chip
                      label={`Every ${localValue.intervalMinutes} min`}
                      size="small"
                      variant="outlined"
                      sx={{ 
                        borderColor: getSelectedInterval().color,
                        color: getSelectedInterval().color,
                        bgcolor: alpha(getSelectedInterval().color, 0.05),
                      }}
                    />
                    {localValue.maxRepetitions && localValue.maxRepetitions > 0 && (
                      <Chip
                        label={`${localValue.maxRepetitions} executions`}
                        size="small"
                        variant="outlined"
                      />
                    )}
                    <Chip
                      label={getSelectedTimezone().displayName}
                      size="small"
                      variant="outlined"
                    />
                  </Box>
                </Box>

                {/* Information Alert */}
                <Alert 
                  severity="info" 
                  sx={{ 
                    mt: 2,
                    borderRadius: 1.5,
                    '& .MuiAlert-icon': {
                      color: theme.palette.info.main,
                    },
                  }}
                >
                  <Typography variant="caption" color="text.secondary">
                    Scheduled syncs will run automatically at the specified intervals. All times are calculated based on the selected timezone.
                  </Typography>
          </Alert>
        </Stack>
      </Box>
    </LocalizationProvider>
  );
};

export default ScheduledSyncConfig;