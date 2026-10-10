'use client';

import { Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';

interface ShowStoredValuesButtonProps {
  loading?: boolean;
  disabled?: boolean;
  onClick: () => void;
}

/** The settings-form control that refetches a config with its stored secrets filled in. */
export function ShowStoredValuesButton({ loading, disabled, onClick }: ShowStoredValuesButtonProps) {
  const { t } = useTranslation();
  // frontend/CLAUDE.md: touch targets are at least 44px on mobile; Radix size="1" is ~24px.
  const isMobile = useIsMobile();

  return (
    <Button
      type="button"
      variant="ghost"
      color="gray"
      size="1"
      loading={loading}
      disabled={disabled}
      style={{ cursor: 'pointer', gap: 6, ...(isMobile ? { minWidth: 44, minHeight: 44 } : null) }}
      onClick={onClick}
    >
      <MaterialIcon name="visibility" size={16} color="var(--gray-11)" />
      {t('form.showStoredValues')}
    </Button>
  );
}
