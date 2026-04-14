'use client';

import React, { useState, useMemo } from 'react';
import { Flex, Box, Text, Button, Popover, IconButton, Tabs } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/**
 * Date filter type for the DateRangePicker component
 * - 'on': Exact date match
 * - 'between': Date range (from-to)
 * - 'before': Dates before the selected date
 * - 'after': Dates after the selected date
 */
export type DateFilterType = 'on' | 'between' | 'before' | 'after';

/**
 * Props for the DateRangePicker component
 */
export interface DateRangePickerProps {
  /** Label text displayed on the trigger button */
  label: string;
  /** Optional Google Material Icon name to display on the trigger button */
  icon?: string;
  /** Start date in ISO format (YYYY-MM-DD) */
  startDate?: string;
  /** End date in ISO format (YYYY-MM-DD) - only used when dateType is 'between' */
  endDate?: string;
  /** Current date filter type (controlled from parent) */
  dateType?: DateFilterType;
  /** Callback fired when date(s) are applied - includes the date type */
  onApply: (startDate: string, endDate: string | undefined, dateType: DateFilterType) => void;
  /** Callback fired when dates are cleared */
  onClear: () => void;
  /** Default tab to show when opening (optional) */
  defaultDateType?: DateFilterType;
}

/**
 * Format date string to display format (DD/MM/YY)
 * @param dateStr - ISO date string (YYYY-MM-DD)
 * @returns Formatted date string
 */
const formatDateForDisplay = (dateStr: string): string => {
  if (!dateStr) return '';
  // Parse ISO date string directly (YYYY-MM-DD) to avoid timezone issues
  const [year, month, day] = dateStr.split('-').map(Number);
  const displayDay = day.toString().padStart(2, '0');
  const displayMonth = month.toString().padStart(2, '0');
  const displayYear = year.toString().slice(-2);
  return `${displayDay}/${displayMonth}/${displayYear}`;
};

/**
 * Format Date object to ISO date string (YYYY-MM-DD)
 * @param date - Date object
 * @returns ISO date string
 */
const formatDateForInput = (date: Date): string => {
  const year = date.getFullYear();
  const month = (date.getMonth() + 1).toString().padStart(2, '0');
  const day = date.getDate().toString().padStart(2, '0');
  return `${year}-${month}-${day}`;
};

/**
 * Get number of days in a given month
 */
const getDaysInMonth = (year: number, month: number): number => {
  return new Date(year, month + 1, 0).getDate();
};

/**
 * Get the day of week (0-6) for the first day of a month
 */
const getFirstDayOfMonth = (year: number, month: number): number => {
  return new Date(year, month, 1).getDay();
};

const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];
const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const DATE_TYPE_LABELS: Record<DateFilterType, string> = {
  on: 'On',
  between: 'Between',
  before: 'Before',
  after: 'After',
};

/**
 * DateRangePicker - A reusable date picker component with type selection tabs
 *
 * @description Provides a calendar-based date picker with tabs for selecting
 * the date filter type (On, Between, Before, After).
 *
 * Features include:
 * - Tabs for selecting date filter type
 * - Custom calendar UI with month navigation
 * - Shows previous/next month days at reduced opacity
 * - Today indicator dot
 * - Visual highlighting for selected dates and ranges
 * - Apply/Clear actions
 * - Formatted date display (DD/MM/YY)
 *
 * @example
 * ```tsx
 * <DateRangePicker
 *   label="Created At"
 *   icon="calendar_today"
 *   startDate={filter.createdAfter}
 *   endDate={filter.createdBefore}
 *   dateType={filter.createdDateType}
 *   onApply={(start, end, type) => setFilter({
 *     createdAfter: start,
 *     createdBefore: end,
 *     createdDateType: type,
 *   })}
 *   onClear={() => setFilter({
 *     createdAfter: undefined,
 *     createdBefore: undefined,
 *     createdDateType: undefined,
 *   })}
 *   defaultDateType="between"
 * />
 * ```
 */
export function DateRangePicker({
  label,
  icon,
  startDate,
  endDate,
  dateType,
  onApply,
  onClear,
  defaultDateType = 'between',
}: DateRangePickerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [currentMonth, setCurrentMonth] = useState(() => {
    if (startDate) return new Date(startDate).getMonth();
    return new Date().getMonth();
  });
  const [currentYear, setCurrentYear] = useState(() => {
    if (startDate) return new Date(startDate).getFullYear();
    return new Date().getFullYear();
  });
  const [selectedStart, setSelectedStart] = useState<string | null>(startDate || null);
  const [selectedEnd, setSelectedEnd] = useState<string | null>(endDate || null);
  const [isSelectingEnd, setIsSelectingEnd] = useState(false);
  const [hoveredDay, setHoveredDay] = useState<string | null>(null);
  const [selectedDateType, setSelectedDateType] = useState<DateFilterType>(
    dateType || defaultDateType
  );

  const hasSelection = dateType === 'between'
    ? !!startDate && !!endDate
    : dateType === 'before'
    ? !!endDate
    : !!startDate;

  // Generate calendar days for the current month (including prev/next month days)
  const calendarDays = useMemo(() => {
    const daysInMonth = getDaysInMonth(currentYear, currentMonth);
    const firstDay = getFirstDayOfMonth(currentYear, currentMonth);
    const prevMonth = currentMonth === 0 ? 11 : currentMonth - 1;
    const prevYear = currentMonth === 0 ? currentYear - 1 : currentYear;
    const prevMonthDays = getDaysInMonth(prevYear, prevMonth);

    const days: Array<{ day: number; isCurrentMonth: boolean }> = [];

    // Previous month trailing days
    for (let i = firstDay - 1; i >= 0; i--) {
      days.push({ day: prevMonthDays - i, isCurrentMonth: false });
    }

    // Current month days
    for (let i = 1; i <= daysInMonth; i++) {
      days.push({ day: i, isCurrentMonth: true });
    }

    // Next month leading days (fill to 42 cells for 6 rows)
    const remaining = 42 - days.length;
    for (let i = 1; i <= remaining; i++) {
      days.push({ day: i, isCurrentMonth: false });
    }

    return days;
  }, [currentMonth, currentYear]);

  // Check if a day is today
  const isToday = (day: number, isCurrentMonth: boolean): boolean => {
    if (!isCurrentMonth) return false;
    const today = new Date();
    return (
      day === today.getDate() &&
      currentMonth === today.getMonth() &&
      currentYear === today.getFullYear()
    );
  };

  // Navigate to previous month
  const goToPreviousMonth = () => {
    if (currentMonth === 0) {
      setCurrentMonth(11);
      setCurrentYear(currentYear - 1);
    } else {
      setCurrentMonth(currentMonth - 1);
    }
  };

  // Navigate to next month
  const goToNextMonth = () => {
    if (currentMonth === 11) {
      setCurrentMonth(0);
      setCurrentYear(currentYear + 1);
    } else {
      setCurrentMonth(currentMonth + 1);
    }
  };

  // Handle day click - supports both single and range selection based on date type
  const handleDayClick = (day: number) => {
    const dateStr = formatDateForInput(new Date(currentYear, currentMonth, day));

    if (selectedDateType !== 'between') {
      // Single date selection for 'on', 'before', 'after'
      setSelectedStart(dateStr);
      setSelectedEnd(null);
    } else {
      // Range selection for 'between'
      if (!selectedStart || !isSelectingEnd) {
        setSelectedStart(dateStr);
        setSelectedEnd(null);
        setIsSelectingEnd(true);
      } else {
        // Set end date, swap if needed
        if (new Date(dateStr) < new Date(selectedStart)) {
          setSelectedEnd(selectedStart);
          setSelectedStart(dateStr);
        } else {
          setSelectedEnd(dateStr);
        }
        setIsSelectingEnd(false);
      }
    }
  };

  // Handle date type tab change
  const handleDateTypeChange = (newType: string) => {
    setSelectedDateType(newType as DateFilterType);
    // Reset end date selection when switching from 'between' to single date mode
    if (newType !== 'between') {
      setSelectedEnd(null);
      setIsSelectingEnd(false);
    }
  };

  // Check if a day is selected (start or end)
  const isDaySelected = (day: number): boolean => {
    const dateStr = formatDateForInput(new Date(currentYear, currentMonth, day));
    return dateStr === selectedStart || dateStr === selectedEnd;
  };

  // Check if a day is within the selected range
  const isDayInRange = (day: number): boolean => {
    if (!selectedStart || !selectedEnd || selectedDateType !== 'between') return false;
    const dateStr = formatDateForInput(new Date(currentYear, currentMonth, day));
    return dateStr > selectedStart && dateStr < selectedEnd;
  };

  // Check if a day is within the hover preview range (between mode, selecting end date)
  const isDayInHoverRange = (day: number): boolean => {
    if (!selectedStart || !hoveredDay || !isSelectingEnd || selectedDateType !== 'between') return false;
    const dateStr = formatDateForInput(new Date(currentYear, currentMonth, day));
    const rangeStart = selectedStart < hoveredDay ? selectedStart : hoveredDay;
    const rangeEnd = selectedStart < hoveredDay ? hoveredDay : selectedStart;
    return dateStr >= rangeStart && dateStr <= rangeEnd;
  };

  // Handle apply button click
  const handleApply = () => {
    if (!selectedStart) return;

    if (selectedDateType === 'between' && (!selectedStart || !selectedEnd)) {
      return; // Need both dates for range
    }

    onApply(selectedStart, selectedEnd || undefined, selectedDateType);
    setIsOpen(false);
  };

  // Handle clear button click
  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedStart(null);
    setSelectedEnd(null);
    onClear();
  };

  // Get formatted date value for the applied chip
  const getDateValue = (): string => {
    if (dateType === 'before') {
      return endDate ? formatDateForDisplay(endDate) : '';
    }
    if (!startDate) return '';
    const formattedStart = formatDateForDisplay(startDate);
    if (dateType === 'between' && endDate) {
      return `${formattedStart} · ${formatDateForDisplay(endDate)}`;
    }
    return formattedStart;
  };

  // Check if Apply button should be disabled
  const isApplyDisabled = selectedDateType === 'between'
    ? !selectedStart || !selectedEnd
    : !selectedStart;

  // Reset state when opening the popover
  const handleOpenChange = (open: boolean) => {
    if (open) {
      setSelectedStart(startDate || null);
      setSelectedEnd(endDate || null);
      setSelectedDateType(dateType || defaultDateType);
      setIsSelectingEnd(false);
      if (startDate) {
        setCurrentMonth(new Date(startDate).getMonth());
        setCurrentYear(new Date(startDate).getFullYear());
      }
    }
    setIsOpen(open);
  };

  return (
    <Popover.Root open={isOpen} onOpenChange={handleOpenChange}>
      <Popover.Trigger>
        {hasSelection ? (
          <Flex
            align="center"
            style={{
              height: '26px',
              border: '1px solid var(--gray-a7)',
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'var(--gray-a3)',
              cursor: 'pointer',
              overflow: 'hidden',
            }}
          >
            {icon && (
              <Flex
                align="center"
                justify="center"
                style={{
                  padding: '0 0 0 8px',
                  height: '100%',
                }}
              >
                <MaterialIcon name={icon} size={14} color="var(--gray-11)" />
              </Flex>
            )}
            <Flex
              align="center"
              style={{
                padding: '0 8px',
                borderRight: '1px solid var(--gray-a7)',
                height: '100%',
              }}
            >
              <Text size="1" style={{ color: 'var(--gray-11)', whiteSpace: 'nowrap' }}>{label}</Text>
            </Flex>
            <Flex
              align="center"
              style={{
                padding: '0 8px',
                borderRight: '1px solid var(--gray-a7)',
                height: '100%',
              }}
            >
              <Text size="1" style={{ color: 'var(--gray-11)', whiteSpace: 'nowrap' }}>
                {dateType ? DATE_TYPE_LABELS[dateType].toLowerCase() : 'on'}
              </Text>
            </Flex>
            <Flex
              align="center"
              style={{
                padding: '0 8px',
                borderRight: '1px solid var(--gray-a7)',
                height: '100%',
              }}
            >
              <Text size="1" style={{ color: 'var(--gray-11)', whiteSpace: 'nowrap' }}>{getDateValue()}</Text>
            </Flex>
            <Flex
              align="center"
              justify="center"
              onClick={handleClear}
              style={{
                padding: '0 4px',
                height: '100%',
                cursor: 'pointer',
              }}
            >
              <MaterialIcon name="close" size={16} color="var(--gray-11)" />
            </Flex>
          </Flex>
        ) : (
          <Button
            variant="outline"
            size="1"
            radius="medium"
            color="gray"
            style={{ height: '24px', gap: '4px', cursor: 'pointer', borderRadius: 'var(--radius-2)' }}
          >
            {icon && (
              <MaterialIcon name={icon} size={14} color="var(--slate-11)" />
            )}
            <Text size="1">{label}</Text>
          </Button>
        )}
      </Popover.Trigger>

      <Popover.Content
        side="bottom"
        align="start"
        sideOffset={4}
        style={{
          padding: '8px',
          minWidth: '280px',
          backgroundColor: 'var(--olive-2)',
          border: '1px solid var(--olive-4)',
          borderRadius: 'var(--radius-2)',
        }}
      >
        {/* Tabs for date type selection */}
        <Tabs.Root
          value={selectedDateType}
          onValueChange={handleDateTypeChange}
        >
          <Tabs.List
            style={{
              display: 'flex',
              boxShadow: 'inset 0 -2px 0 0 var(--slate-6)',
              marginBottom: '8px',
              backgroundColor: 'transparent',
            }}
          >
            {(['on', 'between', 'before', 'after'] as DateFilterType[]).map((type) => (
              <Tabs.Trigger
                key={type}
                value={type}
                style={{
                  flex: 1,
                  padding: '8px 4px',
                  backgroundColor: 'transparent',
                  border: 'none',
                  borderBottom: selectedDateType === type
                    ? '2px solid var(--accent-10)'
                    : '2px solid transparent',
                  marginBottom: '-2px',
                  color: selectedDateType === type
                    ? 'var(--slate-12)'
                    : 'var(--slate-11)',
                  fontWeight: selectedDateType === type ? '500' : '400',
                  fontSize: '12px',
                  cursor: 'pointer',
                }}
              >
                {DATE_TYPE_LABELS[type]}
              </Tabs.Trigger>
            ))}
          </Tabs.List>
        </Tabs.Root>

        {/* Date input display */}
        <Flex
          align="center"
          justify="between"
          style={{
            padding: '8px 16px',
            backgroundColor: 'var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            marginBottom: '16px',
            border: '1px solid var(--olive-4)',
          }}
        >
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {selectedStart
              ? selectedDateType === 'between' && selectedEnd
                ? `${formatDateForDisplay(selectedStart)} - ${formatDateForDisplay(selectedEnd)}`
                : selectedDateType === 'between' && isSelectingEnd && hoveredDay
                ? `${formatDateForDisplay(selectedStart)} - ${formatDateForDisplay(hoveredDay)}`
                : selectedDateType === 'between' && isSelectingEnd
                ? `${formatDateForDisplay(selectedStart)} - End Date`
                : formatDateForDisplay(selectedStart)
              : selectedDateType === 'between'
              ? 'Start Date - End Date'
              : 'Pick a date'}
          </Text>
          <MaterialIcon name="calendar_today" size={20} color="var(--slate-9)" />
        </Flex>

        {/* Calendar container */}
        <Box
          style={{
            backgroundColor: 'var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            padding: '8px',
            border: '1px solid var(--olive-4)',
          }}
        >
          {/* Month navigation */}
          <Flex align="center" justify="between" style={{ marginBottom: '16px', padding: '4px 8px' }}>
            <IconButton
              variant="outline"
              size="1"
              color="gray"
              onClick={goToPreviousMonth}
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon name="chevron_left" size={16} color="var(--slate-11)" />
            </IconButton>
            <Text size="2" weight="bold" style={{ color: 'var(--slate-12)' }}>
              {MONTHS[currentMonth]} {currentYear}
            </Text>
            <IconButton
              variant="outline"
              size="1"
              color="gray"
              onClick={goToNextMonth}
              style={{ cursor: 'pointer' }}
            >
              <MaterialIcon name="chevron_right" size={16} color="var(--slate-11)" />
            </IconButton>
          </Flex>

          {/* Weekday headers */}
          <Box
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(7, 1fr)',
              gap: '0px',
              marginBottom: '4px',
            }}
          >
            {WEEKDAYS.map((day) => (
              <Flex
                key={day}
                align="center"
                justify="center"
                style={{ height: '24px' }}
              >
                <Text size="2" style={{ color: 'var(--slate-11)' }}>
                  {day}
                </Text>
              </Flex>
            ))}
          </Box>

          {/* Calendar days */}
          <Box
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(7, 1fr)',
              gap: '0px',
            }}
          >
            {calendarDays.map(({ day, isCurrentMonth }, index) => (
              <Flex
                key={index}
                direction="column"
                align="center"
                justify="center"
                onClick={() => isCurrentMonth && handleDayClick(day)}
                onMouseEnter={() => {
                  if (isCurrentMonth && isSelectingEnd && selectedDateType === 'between') {
                    setHoveredDay(formatDateForInput(new Date(currentYear, currentMonth, day)));
                  }
                }}
                onMouseLeave={() => {
                  if (hoveredDay) setHoveredDay(null);
                }}
                style={{
                  height: '36px',
                  width: '36px',
                  cursor: isCurrentMonth ? 'pointer' : 'default',
                  opacity: isCurrentMonth ? 1 : 0.3,
                  backgroundColor: isCurrentMonth && isDaySelected(day)
                    ? 'var(--accent-5)'
                    : isCurrentMonth && isDayInRange(day)
                    ? 'var(--accent-5)'
                    : isCurrentMonth && isDayInHoverRange(day)
                    ? 'var(--accent-3)'
                    : 'transparent',
                }}
              >
                <Text
                  size="2"
                  style={{
                    color: 'var(--slate-12)',
                  }}
                >
                  {day}
                </Text>
                {isToday(day, isCurrentMonth) && (
                  <Box
                    style={{
                      width: '4px',
                      height: '4px',
                      borderRadius: '50%',
                      backgroundColor: 'var(--accent-5)',
                      marginTop: '2px',
                    }}
                  />
                )}
              </Flex>
            ))}
          </Box>
        </Box>

        {/* Apply button */}
        <Flex style={{ padding: '8px 0 0 0' }}>
          <Button
            variant="solid"
            size="1"
            style={{
              cursor: isApplyDisabled ? 'not-allowed' : 'pointer',
              width: '100%',
              height: 'var(--space-6)',
              ...(!isApplyDisabled ? { backgroundColor: 'var(--accent-8)' } : {}),
            }}
            disabled={isApplyDisabled}
            onClick={handleApply}
          >
            Apply
          </Button>
        </Flex>
      </Popover.Content>
    </Popover.Root>
  );
}
