'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, Flex, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { McpServerTemplate } from '../../types';
import { offeredForPersonalServers } from '../../catalog-replacement';

interface McpAddServerDialogProps {
  open: boolean;
  templates: McpServerTemplate[];
  onOpenChange: (open: boolean) => void;
  onPickTemplate: (template: McpServerTemplate) => void;
  onPickCustom: () => void;
}

/** Picks what a user's own server is based on. Local-command (STDIO) servers are left out:
 * only an administrator may run a process on the server. */
export function McpAddServerDialog({
  open,
  templates,
  onOpenChange,
  onPickTemplate,
  onPickCustom,
}: McpAddServerDialogProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState('');

  useEffect(() => {
    if (open) setQuery('');
  }, [open]);

  const remoteTemplates = useMemo(() => {
    const q = query.trim().toLowerCase();
    return offeredForPersonalServers(templates)
      .filter(
        (tpl) =>
          !q ||
          tpl.displayName.toLowerCase().includes(q) ||
          tpl.description.toLowerCase().includes(q) ||
          tpl.tags.some((tag) => tag.toLowerCase().includes(q))
      )
      .sort((a, b) => a.displayName.localeCompare(b.displayName, undefined, { sensitivity: 'base' }));
  }, [templates, query]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content
        style={{
          maxWidth: '34rem',
          padding: 'var(--space-5)',
          backgroundColor: 'var(--color-panel-solid)',
          borderRadius: 'var(--radius-5)',
          border: '1px solid var(--olive-a3)',
        }}
      >
        <Dialog.Title style={{ color: 'var(--slate-12)' }}>
          {t('workspace.mcpServers.addDialog.title')}
        </Dialog.Title>
        <Dialog.Description size="2" style={{ color: 'var(--slate-11)', marginTop: 4 }}>
          {t('workspace.mcpServers.addDialog.description')}
        </Dialog.Description>

        <Flex direction="column" gap="3" mt="4">
          <TextField.Root
            size="2"
            placeholder={t('workspace.mcpServers.searchPlaceholder')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          >
            <TextField.Slot>
              <MaterialIcon name="search" size={16} color="var(--gray-9)" />
            </TextField.Slot>
          </TextField.Root>

          <Flex direction="column" gap="1" style={{ maxHeight: 360, overflowY: 'auto' }}>
            <PickerRow
              icon={<MaterialIcon name="add_link" size={16} color="var(--accent-11)" />}
              title={t('workspace.mcpServers.addDialog.custom')}
              description={t('workspace.mcpServers.addDialog.customDescription')}
              onClick={onPickCustom}
            />
            {remoteTemplates.map((tpl) => (
              <PickerRow
                key={tpl.typeId}
                icon={
                  tpl.icon ? (
                    <img src={tpl.icon} alt="" width={16} height={16} />
                  ) : (
                    <MaterialIcon name="hub" size={16} color="var(--gray-10)" />
                  )
                }
                title={tpl.displayName}
                description={tpl.description}
                onClick={() => onPickTemplate(tpl)}
              />
            ))}
            {remoteTemplates.length === 0 && query.trim() && (
              <Text size="2" style={{ color: 'var(--gray-10)', padding: 'var(--space-2)' }}>
                {t('workspace.mcpServers.noResults')}
              </Text>
            )}
          </Flex>
        </Flex>

        <Flex justify="end" mt="4">
          <Dialog.Close>
            <Text as="span" size="2" style={{ padding: '8px 14px', cursor: 'pointer', color: 'var(--gray-11)' }}>
              {t('common.cancel')}
            </Text>
          </Dialog.Close>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}

function PickerRow({
  icon,
  title,
  description,
  onClick,
}: {
  icon: ReactNode;
  title: string;
  description: string;
  onClick: () => void;
}) {
  const [isHovered, setIsHovered] = useState(false);
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        appearance: 'none',
        margin: 0,
        font: 'inherit',
        textAlign: 'left',
        border: 'none',
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-3)',
        width: '100%',
        padding: 'var(--space-2)',
        borderRadius: 'var(--radius-2)',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'transparent',
        cursor: 'pointer',
        transition: 'background-color 150ms ease',
      }}
    >
      <Flex
        align="center"
        justify="center"
        style={{
          width: 32,
          height: 32,
          backgroundColor: 'var(--gray-a2)',
          borderRadius: 'var(--radius-1)',
          flexShrink: 0,
          overflow: 'hidden',
        }}
      >
        {icon}
      </Flex>
      <Flex direction="column" gap="0" style={{ minWidth: 0 }}>
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {title}
        </Text>
        <Text
          size="1"
          style={{ color: 'var(--gray-10)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
        >
          {description}
        </Text>
      </Flex>
    </button>
  );
}
