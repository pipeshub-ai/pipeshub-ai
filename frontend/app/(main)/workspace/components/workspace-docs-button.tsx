'use client';

import { Button } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/** Header "Docs" link button: labelled, so it doesn't read as a bare icon. */
export function WorkspaceDocsButton({ href }: { href: string }) {
  const { t } = useTranslation();
  return (
    <Button
      size="2"
      variant="outline"
      color="gray"
      onClick={() => window.open(href, '_blank', 'noopener,noreferrer')}
      style={{ cursor: 'pointer' }}
    >
      <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
      {t('common.docs')}
    </Button>
  );
}
