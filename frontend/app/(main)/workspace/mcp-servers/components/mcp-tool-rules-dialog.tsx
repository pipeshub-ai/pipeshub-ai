'use client';

import { useTranslation } from 'react-i18next';
import { Button, Dialog, Flex } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import type { McpRuleTool } from '../tool-rules';
import { McpToolRulesList, useToolRulesEditor } from './mcp-tool-rules-editor';

interface McpToolRulesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  instanceId: string;
  serverName: string;
  /** What the server offers now; may be empty when it can't be listed. */
  tools: McpRuleTool[];
  /** Portal host, to stack above a workspace drawer (`useWorkspaceDrawerNestedModalHost`). */
  container?: HTMLElement | null;
}

/** The person's own rules for a server's tools: Pre-approved, Allow on approval or Deny. */
export function McpToolRulesDialog({ open, onOpenChange, instanceId, serverName, tools, container }: McpToolRulesDialogProps) {
  const { t } = useTranslation();
  const editor = useToolRulesEditor(instanceId, tools, open);

  const save = async () => {
    if (await editor.save()) onOpenChange(false);
  };

  return (
    <Dialog.Root open={open} onOpenChange={(v) => !editor.saving && onOpenChange(v)}>
      <Dialog.Content
        container={container ?? undefined}
        style={{
          maxWidth: '44rem',
          padding: 'var(--space-5)',
          backgroundColor: 'var(--color-panel-solid)',
          borderRadius: 'var(--radius-5)',
          border: '1px solid var(--olive-a3)',
        }}
      >
        <Dialog.Title style={{ color: 'var(--slate-12)' }}>
          {t('workspace.mcpServers.toolRules.title', { name: serverName })}
        </Dialog.Title>
        <Dialog.Description size="2" style={{ color: 'var(--slate-11)', marginTop: 4 }}>
          {t('workspace.mcpServers.toolRules.description.personal')}
        </Dialog.Description>

        <Flex direction="column" mt="4">
          <McpToolRulesList editor={editor} maxListHeight="55vh" />
        </Flex>

        <Flex justify="end" gap="2" mt="5">
          <Dialog.Close>
            <Button type="button" variant="soft" color="gray" size="2" disabled={editor.saving}>
              {t('common.cancel')}
            </Button>
          </Dialog.Close>
          <LoadingButton
            type="button"
            variant="solid"
            size="2"
            disabled={editor.loading || Boolean(editor.loadError) || editor.changes === 0}
            loading={editor.saving}
            loadingLabel={t('common.saving')}
            onClick={() => void save()}
          >
            {t('common.save')}
          </LoadingButton>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
