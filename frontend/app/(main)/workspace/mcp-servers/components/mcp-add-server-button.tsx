'use client';

import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

export function McpAddServerButton({ onClick }: { onClick: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        outline: 'none',
        border: 'none',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        height: 'var(--space-6)',
        padding: '0 12px',
        borderRadius: 'var(--radius-2)',
        backgroundColor: 'var(--accent-9)',
        cursor: 'pointer',
      }}
    >
      <MaterialIcon name="add" size={16} color="white" />
      <span style={{ fontSize: 14, fontWeight: 500, color: 'white' }}>
        {t('workspace.mcpServers.addServer')}
      </span>
    </button>
  );
}
