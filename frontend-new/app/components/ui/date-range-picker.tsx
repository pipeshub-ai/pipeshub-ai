'use client';

import React, { useState, useMemo, useEffect } from 'react';
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
  /**
   * When true, values use local `YYYY-MM-DDTHH:mm` and a time control is shown for each active boundary.
   * Date-only `YYYY-MM-DD` props still work; time defaults to `00:00`.
   */
  withTime?: boolean;
  /** Hide type tabs and keep this mode (e.g. connector filters where the operator is fixed). */
  fixedDateType?: DateFilterType;
  /** Portal container for popover content (e.g. nested panel) so the menu stacks correctly. */
  portalContainer?: HTMLElement | null;
  /**
   * `toolbar`: multi-segment trigger when a value is set (e.g. users filters).
   * `field`: one outline control, full width, value text + ellipsis (e.g. connector sync filters).
   */
  triggerVariant?: 'toolbar' | 'field';
  /**
   * When `triggerVariant` is `field` and there is a selection, show a Clear control under the trigger
   * (same idea as `FilterDropdown` `summaryBelowTrigger`).
   */
  summaryBelowTrigger?: boolean;
}

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

/** Calendar / range comparisons use the date portion only. */
function dateKey(iso: string | null): string {
  if (!iso) return '';
  return iso.slice(0, 10);
}

function parseDateTimeBoundary(s: string | undefined | null): { date: string; time: string } {
  if (!s?.trim()) return { date: '', time: '00:00' };
  const tIdx = s.indexOf('T');
  if (tIdx >= 0) {
    const date = s.slice(0, 10);
    const rest = s.slice(tIdx + 1);
    const hm = rest.slice(0, 5);
    return { date, time: /^\d{2}:\d{2}$/.test(hm) ? hm : '00:00' };
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return { date: s, time: '00:00' };
  return { date: '', time: '00:00' };
}

function mergeDateTime(dateYYYYMMDD: string, timeHHmm: string): string {
  if (!dateYYYYMMDD) return '';
  const t = (timeHHmm || '00:00').slice(0, 5);
  return `${dateYYYYMMDD}T${t}`;
}

function formatBoundaryForDisplay(raw: string, withTime: boolean): string {
  if (!raw) return '';
  const d = dateKey(raw);
  if (!d) return '';
  const [year, month, day] = d.split('-').map(Number);
  const displayDay = day.toString().padStart(2, '0');
  const displayMonth = month.toString().padStart(2, '0');
  const displayYear = year.toString().slice(-2);
  const datePart = `${displayDay}/${displayMonth}/${displayYear}`;
  if (withTime && raw.includes('T')) {
    const { time } = parseDateTimeBoundary(raw);
    return `${datePart} · ${time}`;
  }
  return datePart;
}

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
  withTime = false,
  fixedDateType,
  portalContainer,
  triggerVariant = 'toolbar',
  summaryBelowTrigger = false,
}: DateRangePickerProps) {
  const effectiveDateType = fixedDateType ?? dateType ?? defaultDateType;

  const [isOpen, setIsOpen] = useState(false);
  const anchorForCalendar = (s?: string, e?: string) => (s || e || '').slice(0, 10);
  const [currentMonth, setCurrentMonth] = useState(() => {
    const a = anchorForCalendar(startDate, endDate);
    if (a) return new Date(a).getMonth();
    return new Date().getMonth();
  });
  const [currentYear, setCurrentYear] = useState(() => {
    const a = anchorForCalendar(startDate, endDate);
    if (a) return new Date(a).getFullYear();
    return new Date().getFullYear();
  });
  const [selectedStart, setSelectedStart] = useState<string | null>(startDate || null);
  const [selectedEnd, setSelectedEnd] = useState<string | null>(endDate || null);
  const [isSelectingEnd, setIsSelectingEnd] = useState(false);
  const [hoveredDay, setHoveredDay] = useState<string | null>(null);
  const [selectedDateType, setSelectedDateType] = useState<DateFilterType>(effectiveDateType);

  useEffect(() => {
    if (fixedDateType) setSelectedDateType(fixedDateType);
    else if (dateType) setSelectedDateType(dateType);
  }, [fixedDateType, dateType]);

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
      const prevSource =
        selectedDateType === 'before' ? selectedStart || selectedEnd : selectedStart;
      const { time: prevTime } = parseDateTimeBoundary(prevSource);
      const merged = withTime ? mergeDateTime(dateStr, prevTime) : dateStr;
      setSelectedStart(merged);
      setSelectedEnd(null);
    } else {
      if (!selectedStart || !isSelectingEnd) {
        const { time: startTime } = parseDateTimeBoundary(selectedStart);
        const merged = withTime ? mergeDateTime(dateStr, startTime || '00:00') : dateStr;
        setSelectedStart(merged);
        setSelectedEnd(null);
        setIsSelectingEnd(true);
      } else {
        const { time: endTime } = parseDateTimeBoundary(selectedEnd);
        const endMerged = withTime ? mergeDateTime(dateStr, endTime || '00:00') : dateStr;
        const startKey = dateKey(selectedStart);
        if (dateStr < startKey) {
          setSelectedEnd(selectedStart);
          setSelectedStart(endMerged);
        } else {
          setSelectedEnd(endMerged);
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
    return dateStr === dateKey(selectedStart) || dateStr === dateKey(selectedEnd);
  };

  // Check if a day is within the selected range
  const isDayInRange = (day: number): boolean => {
    if (!selectedStart || !selectedEnd || selectedDateType !== 'between') return false;
    const dateStr = formatDateForInput(new Date(currentYear, currentMonth, day));
    const a = dateKey(selectedStart);
    const b = dateKey(selectedEnd);
    return dateStr > a && dateStr < b;
  };

  // Check if a day is within the hover preview range (between mode, selecting end date)
  const isDayInHoverRange = (day: number): boolean => {
    if (!selectedStart || !hoveredDay || !isSelectingEnd || selectedDateType !== 'between') return false;
    const dateStr = formatDateForInput(new Date(currentYear, currentMonth, day));
    const hk = dateKey(hoveredDay);
    const sk = dateKey(selectedStart);
    const rangeStart = sk < hk ? sk : hk;
    const rangeEnd = sk < hk ? hk : sk;
    return dateStr >= rangeStart && dateStr <= rangeEnd;
  };

  // Handle apply button click
  const handleApply = () => {
    if (selectedDateType === 'between') {
      if (!selectedStart || !selectedEnd) return;
      onApply(selectedStart, selectedEnd, selectedDateType);
      setIsOpen(false);
      return;
    }
    const boundary =
      selectedDateType === 'before' ? selectedStart || selectedEnd : selectedStart || selectedEnd;
    if (!boundary) return;
    if (selectedDateType === 'before') {
      onApply(boundary, undefined, 'before');
    } else {
      const out =
        selectedDateType === 'after' || selectedDateType === 'on'
          ? selectedStart || selectedEnd
          : boundary;
      if (!out) return;
      onApply(out, undefined, selectedDateType);
    }
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
      return endDate ? formatBoundaryForDisplay(endDate, withTime) : '';
    }
    if (!startDate) return '';
    const formattedStart = formatBoundaryForDisplay(startDate, withTime);
    if (dateType === 'between' && endDate) {
      return `${formattedStart} · ${formatBoundaryForDisplay(endDate, withTime)}`;
    }
    return formattedStart;
  };

  /** Single-line summary for field-style triggers (operator mode + stored value). */
  const getFieldTriggerSummary = (): string => {
    const valuePart = getDateValue();
    if (!dateType) return valuePart;
    const mode = DATE_TYPE_LABELS[dateType];
    return valuePart ? `${mode} · ${valuePart}` : mode;
  };

  // Check if Apply button should be disabled
  const isApplyDisabled =
    selectedDateType === 'between'
      ? !selectedStart || !selectedEnd
      : selectedDateType === 'before'
        ? !(selectedStart || selectedEnd)
        : !selectedStart;

  // Reset state when opening the popover
  const handleOpenChange = (open: boolean) => {
    if (open) {
      let nextStart = startDate || null;
      let nextEnd = endDate || null;
      const eff = fixedDateType ?? dateType ?? defaultDateType;
      if (eff === 'before' && nextEnd && !nextStart) {
        nextStart = nextEnd;
        nextEnd = null;
      }
      setSelectedStart(nextStart);
      setSelectedEnd(nextEnd);
      setSelectedDateType(fixedDateType ?? dateType ?? defaultDateType);
      setIsSelectingEnd(false);
      const anchor = anchorForCalendar(nextStart ?? undefined, nextEnd ?? undefined);
      if (anchor) {
        const d = new Date(anchor);
        setCurrentMonth(d.getMonth());
        setCurrentYear(d.getFullYear());
      }
    }
    setIsOpen(open);
  };

  const fieldClearRow = summaryBelowTrigger && hasSelection && (
    <Flex justify="end" style={{ width: '100%' }}>
      <Button
        type="button"
        variant="ghost"
        color="gray"
        size="1"
        style={{ cursor: 'pointer' }}
        onClick={(e) => {
          e.stopPropagation();
          handleClear(e);
        }}
      >
        Clear
      </Button>
    </Flex>
  );

  const triggerToolbar = (
    <>
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
    </>
  );

  const triggerField = (
    <Flex direction="column" gap="2" style={{ width: '100%', minWidth: 0 }}>
      <Popover.Trigger asChild>
        <Button
          type="button"
          variant="outline"
          color="gray"
          size="2"
          title={hasSelection ? getFieldTriggerSummary() : label}
          aria-label={hasSelection ? getFieldTriggerSummary() : label}
          style={{
            minHeight: 32,
            width: '100%',
            minWidth: 0,
            maxWidth: '100%',
            gap: 8,
            cursor: 'pointer',
            borderRadius: 'var(--radius-2)',
            justifyContent: 'flex-start',
            alignItems: 'center',
          }}
        >
          {icon && (
            <MaterialIcon name={icon} size={16} color="var(--slate-11)" style={{ flexShrink: 0 }} />
          )}
          <Box style={{ minWidth: 0, flex: 1, overflow: 'hidden', textAlign: 'left' }}>
            <Text
              size="2"
              style={{
                color: 'var(--gray-12)',
                display: 'block',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {hasSelection ? getFieldTriggerSummary() : label}
            </Text>
          </Box>
        </Button>
      </Popover.Trigger>
      {summaryBelowTrigger && hasSelection ? fieldClearRow : null}
    </Flex>
  );

  return (
    <Popover.Root open={isOpen} onOpenChange={handleOpenChange}>
      {triggerVariant === 'field' ? (
        triggerField
      ) : (
        <Popover.Trigger>{triggerToolbar}</Popover.Trigger>
      )}

      <Popover.Content
        side="bottom"
        align="start"
        sideOffset={4}
        container={portalContainer ?? undefined}
        style={{
          padding: '8px',
          minWidth: '280px',
          backgroundColor: 'var(--olive-2)',
          border: '1px solid var(--olive-4)',
          borderRadius: 'var(--radius-2)',
        }}
      >
        {!fixedDateType ? (
          <Tabs.Root value={selectedDateType} onValueChange={handleDateTypeChange}>
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
                    borderBottom:
                      selectedDateType === type
                        ? '2px solid var(--accent-10)'
                        : '2px solid transparent',
                    marginBottom: '-2px',
                    color: selectedDateType === type ? 'var(--slate-12)' : 'var(--slate-11)',
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
        ) : null}

        {/* Date input display */}
        <Flex
          align="center"
          justify="between"
          style={{
            padding: '8px 16px',
            backgroundColor: 'var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            marginBottom: withTime ? '8px' : '16px',
            border: '1px solid var(--olive-4)',
          }}
        >
          <Text size="2" style={{ color: 'var(--slate-11)' }}>
            {(() => {
              const primary = selectedStart || selectedEnd;
              if (selectedDateType === 'between' && !primary) return 'Start Date - End Date';
              if (!primary) return 'Pick a date';
              if (selectedDateType === 'between' && selectedStart && selectedEnd) {
                return `${formatBoundaryForDisplay(selectedStart, withTime)} - ${formatBoundaryForDisplay(selectedEnd, withTime)}`;
              }
              if (selectedDateType === 'between' && isSelectingEnd && hoveredDay) {
                return `${formatBoundaryForDisplay(selectedStart!, withTime)} - ${formatBoundaryForDisplay(hoveredDay, withTime)}`;
              }
              if (selectedDateType === 'between' && isSelectingEnd) {
                return `${formatBoundaryForDisplay(selectedStart!, withTime)} - End Date`;
              }
              return formatBoundaryForDisplay(primary, withTime);
            })()}
          </Text>
          <MaterialIcon name="calendar_today" size={20} color="var(--slate-9)" />
        </Flex>

        {withTime ? (
          <Flex direction="column" gap="2" style={{ marginBottom: '12px' }}>
            {selectedDateType === 'between' ? (
              <Flex align="center" gap="3" wrap="wrap">
                <Flex direction="column" gap="1" style={{ flex: '1 1 120px', minWidth: 0 }}>
                  <Text size="1" style={{ color: 'var(--slate-11)' }}>
                    Start time
                  </Text>
                  <input
                    type="time"
                    step={60}
                    disabled={!selectedStart}
                    value={parseDateTimeBoundary(selectedStart).time}
                    onChange={(e) => {
                      const { date } = parseDateTimeBoundary(selectedStart);
                      if (!date) return;
                      setSelectedStart(mergeDateTime(date, e.target.value));
                    }}
                    style={{
                      height: 32,
                      width: '100%',
                      padding: '4px 8px',
                      borderRadius: 'var(--radius-2)',
                      border: '1px solid var(--gray-a5)',
                      fontSize: 14,
                      boxSizing: 'border-box',
                      backgroundColor: 'var(--color-surface)',
                    }}
                  />
                </Flex>
                <Flex direction="column" gap="1" style={{ flex: '1 1 120px', minWidth: 0 }}>
                  <Text size="1" style={{ color: 'var(--slate-11)' }}>
                    End time
                  </Text>
                  <input
                    type="time"
                    step={60}
                    disabled={!selectedEnd}
                    value={parseDateTimeBoundary(selectedEnd).time}
                    onChange={(e) => {
                      const { date } = parseDateTimeBoundary(selectedEnd);
                      if (!date) return;
                      setSelectedEnd(mergeDateTime(date, e.target.value));
                    }}
                    style={{
                      height: 32,
                      width: '100%',
                      padding: '4px 8px',
                      borderRadius: 'var(--radius-2)',
                      border: '1px solid var(--gray-a5)',
                      fontSize: 14,
                      boxSizing: 'border-box',
                      backgroundColor: 'var(--color-surface)',
                    }}
                  />
                </Flex>
              </Flex>
            ) : (
              <Flex direction="column" gap="1" style={{ width: '100%' }}>
                <Text size="1" style={{ color: 'var(--slate-11)' }}>
                  Time
                </Text>
                <input
                  type="time"
                  step={60}
                  disabled={!(selectedStart || selectedEnd)}
                  value={parseDateTimeBoundary(selectedStart || selectedEnd).time}
                  onChange={(e) => {
                    const src = selectedStart || selectedEnd;
                    const { date } = parseDateTimeBoundary(src);
                    if (!date) return;
                    const merged = mergeDateTime(date, e.target.value);
                    if (selectedDateType === 'before') {
                      setSelectedStart(merged);
                      setSelectedEnd(null);
                    } else {
                      setSelectedStart(merged);
                    }
                  }}
                  style={{
                    height: 32,
                    maxWidth: 200,
                    padding: '4px 8px',
                    borderRadius: 'var(--radius-2)',
                    border: '1px solid var(--gray-a5)',
                    fontSize: 14,
                    boxSizing: 'border-box',
                    backgroundColor: 'var(--color-surface)',
                  }}
                />
              </Flex>
            )}
          </Flex>
        ) : null}

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
