'use client';

import { useTranslation } from 'react-i18next';
import { Button, Dialog, Flex, Text } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import type { McpRuleTool } from '../tool-rules';
import { McpToolRulesList, useToolRulesEditor, type McpToolRulesTarget } from './mcp-tool-rules-editor';

export type { McpToolRulesTarget } from './mcp-tool-rules-editor';

interface McpToolRulesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  target: McpToolRulesTarget;
  serverName: string;
  /** What the server offers now; may be empty when it can't be listed. */
  tools: McpRuleTool[];
  /** Shown but not changeable (someone who can't edit the agent). */
  readOnly?: boolean;
  /** Portal host, to stack above a workspace drawer (`useWorkspaceDrawerNestedModalHost`). */
  container?: HTMLElement | null;
}

/** Pre-approved / Allow on approval / Deny per tool, for a person's chats, an agent, or (as a floor) the company. */
export function McpToolRulesDialog({
  open,
  onOpenChange,
  target,
  serverName,
  tools,
  readOnly = false,
  container,
}: McpToolRulesDialogProps) {
  const { t } = useTranslation();
  const editor = useToolRulesEditor(target, tools, open);

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
          {t(`workspace.mcpServers.toolRules.description.${target.kind}`)}
        </Dialog.Description>
        {editor.isCompany && (
          <Text as="p" size="1" mt="2" style={{ color: 'var(--slate-10)' }}>
            {t('workspace.mcpServers.toolRules.unattendedHint')}
          </Text>
        )}
        {readOnly && (
          <Text as="p" size="1" mt="2" style={{ color: 'var(--slate-10)' }}>
            {t('workspace.mcpServers.toolRules.viewOnly')}
          </Text>
        )}

        <Flex direction="column" mt="4">
          <McpToolRulesList editor={editor} readOnly={readOnly} maxListHeight="50vh" />
        </Flex>

        <Flex justify="end" gap="2" mt="5">
          <Dialog.Close>
            <Button type="button" variant="soft" color="gray" size="2" disabled={editor.saving}>
              {readOnly ? t('common.close') : t('common.cancel')}
            </Button>
          </Dialog.Close>
          {!readOnly && (
            <LoadingButton
              type="button"
              variant="solid"
              size="2"
              disabled={editor.loading || Boolean(editor.loadError)}
              loading={editor.saving}
              loadingLabel={t('common.saving')}
              onClick={() => void save()}
            >
              {t('common.save')}
            </LoadingButton>
          )}
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
