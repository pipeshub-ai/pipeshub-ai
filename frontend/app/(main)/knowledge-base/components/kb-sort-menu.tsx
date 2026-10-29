'use client';

import { Button, DropdownMenu, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

/** The sorts the records API accepts that the table also offers on its column headers. */
export type KbSortMenuField = 'name' | 'createdAt' | 'updatedAt' | 'size';

export interface KbSortMenuValue {
  field: string;
  order: 'asc' | 'desc';
}

const FIELDS: { value: KbSortMenuField; labelKey: string }[] = [
  { value: 'name', labelKey: 'table.fileName' },
  { value: 'createdAt', labelKey: 'table.created' },
  { value: 'updatedAt', labelKey: 'table.updated' },
  { value: 'size', labelKey: 'table.size' },
];

/**
 * "Sort: Updated ↓" chip for the records filter bar. Applies straight away, like the
 * sortable column headers, and shows whatever they last set.
 */
export function KbSortMenu({
  value,
  onChange,
}: {
  value: KbSortMenuValue;
  onChange: (next: { field: KbSortMenuField; order: 'asc' | 'desc' }) => void;
}) {
  const { t } = useTranslation();
  // Phones get 44px rows, like the row action menus
  const itemStyle = useIsMobile() ? { height: 44 } : undefined;
  const current = FIELDS.find((f) => f.value === value.field);
  const label = current ? `${t('filter.sort')}: ${t(current.labelKey)}` : t('filter.sort');

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger>
        <Button
          className="touch-hit"
          variant="outline"
          size="1"
          radius="medium"
          color="gray"
          aria-label={label}
          style={{
            height: 28,
            padding: '0 6px 0 10px',
            gap: 6,
            cursor: 'pointer',
            borderRadius: 'var(--radius-2)',
            border: '1px solid var(--olive-4)',
            boxShadow: 'none',
            flexShrink: 0,
          }}
        >
          <MaterialIcon
            name={value.order === 'asc' ? 'arrow_upward' : 'arrow_downward'}
            size={14}
            color="var(--neutral-11)"
          />
          <Text size="1" style={{ fontWeight: 400, lineHeight: '18px', color: 'var(--neutral-11)', whiteSpace: 'nowrap' }}>
            {label}
          </Text>
          <MaterialIcon name="expand_more" size={14} color="var(--neutral-11)" />
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Content size="1" align="start" sideOffset={4} style={{ minWidth: 178 }}>
        <DropdownMenu.RadioGroup
          value={current?.value ?? ''}
          onValueChange={(field) => onChange({ field: field as KbSortMenuField, order: value.order })}
        >
          {FIELDS.map((f) => (
            <DropdownMenu.RadioItem key={f.value} value={f.value} style={itemStyle}>
              {t(f.labelKey)}
            </DropdownMenu.RadioItem>
          ))}
        </DropdownMenu.RadioGroup>
        <DropdownMenu.Separator />
        <DropdownMenu.RadioGroup
          value={value.order}
          // Keep the active field even when it is one the menu does not list (e.g. Collections' type sort)
          onValueChange={(order) =>
            onChange({ field: value.field as KbSortMenuField, order: order as 'asc' | 'desc' })
          }
        >
          <DropdownMenu.RadioItem value="asc" style={itemStyle}>
            <MaterialIcon name="arrow_upward" size={14} color="var(--slate-11)" />
            {t('filter.sortAscending')}
          </DropdownMenu.RadioItem>
          <DropdownMenu.RadioItem value="desc" style={itemStyle}>
            <MaterialIcon name="arrow_downward" size={14} color="var(--slate-11)" />
            {t('filter.sortDescending')}
          </DropdownMenu.RadioItem>
        </DropdownMenu.RadioGroup>
      </DropdownMenu.Content>
    </DropdownMenu.Root>
  );
}
