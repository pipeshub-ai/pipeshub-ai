'use client';

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Dialog, Flex, Text } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { ToolsetsApi } from '@/app/(main)/toolsets/api';
import {
  McpToolRulesList,
  useToolRulesEditor,
  type ToolRulesApi,
} from '@/app/(main)/workspace/mcp-servers/components/mcp-tool-rules-editor';
import type { McpRuleTool } from '@/app/(main)/workspace/mcp-servers/tool-rules';
import { ruleToolsFromSchema } from '../utils/tool-names-from-schema';

/** A toolset instance's rules: an org action's are set by an admin, a personal one's by its owner. */
const TOOLSET_RULES_API: ToolRulesApi = {
  get: (instanceId) => ToolsetsApi.getToolRules(instanceId),
  save: (instanceId, rules) => ToolsetsApi.updateToolRules(instanceId, rules),
};

interface ToolsetToolRulesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  instanceId: string;
  instanceName: string;
  toolsetType: string;
  /** The caller's own action, whose rules are theirs alone. */
  personal?: boolean;
  /** Portal host, to stack above a workspace drawer (`useWorkspaceDrawerNestedModalHost`). */
  container?: HTMLElement | null;
}

/** What happens when an assistant or agent wants to use one of a toolset's tools: Pre-approved,
 * Allow on approval or Deny. An admin changes an org action's, the owner a personal one's; everyone else sees them. */
export function ToolsetToolRulesDialog({
  open,
  onOpenChange,
  instanceId,
  instanceName,
  toolsetType,
  personal = false,
  container,
}: ToolsetToolRulesDialogProps) {
  const { t } = useTranslation();
  const [tools, setTools] = useState<McpRuleTool[] | null>(null);
  const [toolsFailed, setToolsFailed] = useState(false);

  useEffect(() => {
    if (!open || !toolsetType) return;
    let cancelled = false;
    setTools(null);
    setToolsFailed(false);
    ToolsetsApi.getToolsetRegistrySchema(toolsetType)
      .then((raw) => {
        if (!cancelled) setTools(ruleToolsFromSchema(raw));
      })
      .catch(() => {
        if (!cancelled) setToolsFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [open, toolsetType]);

  const editor = useToolRulesEditor(instanceId, tools ?? [], open, {
    api: TOOLSET_RULES_API,
    listedOnly: true,
    source: 'toolset',
  });
  const ready = tools !== null && !editor.loading && !editor.loadError;
  const editable = ready && editor.canEdit;

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
          {t('workspace.actions.toolRules.title', { name: instanceName })}
        </Dialog.Title>
        <Dialog.Description size="2" style={{ color: 'var(--slate-11)', marginTop: 4 }}>
          {t(personal ? 'workspace.mcpServers.toolRules.description.personal' : 'workspace.actions.toolRules.description')}
        </Dialog.Description>
        {ready && !editor.canEdit && (
          <Text as="p" size="2" mt="2" style={{ color: 'var(--slate-11)' }}>
            {t('workspace.actions.toolRules.readOnly')}
          </Text>
        )}

        <Flex direction="column" mt="4">
          {toolsFailed ? (
            <Text as="p" size="2" role="alert" style={{ color: 'var(--red-11)' }}>
              {t('workspace.actions.toolRules.toolsError')}
            </Text>
          ) : tools === null ? (
            <Text as="p" size="2" style={{ color: 'var(--slate-10)' }}>
              {t('workspace.mcpServers.toolRules.loading')}
            </Text>
          ) : tools.length === 0 ? (
            <Text as="p" size="2" style={{ color: 'var(--slate-10)' }}>
              {t('workspace.mcpServers.toolRules.noTools')}
            </Text>
          ) : (
            <McpToolRulesList editor={editor} maxListHeight="55vh" />
          )}
        </Flex>

        <Flex justify="end" gap="2" mt="5">
          {editable ? (
            <>
              <Dialog.Close>
                <Button type="button" variant="soft" color="gray" size="2" disabled={editor.saving}>
                  {t('common.cancel')}
                </Button>
              </Dialog.Close>
              <LoadingButton
                type="button"
                variant="solid"
                size="2"
                disabled={editor.changes === 0}
                loading={editor.saving}
                loadingLabel={t('common.saving')}
                onClick={() => void save()}
              >
                {t('common.save')}
              </LoadingButton>
            </>
          ) : (
            <Dialog.Close>
              <Button type="button" variant="soft" color="gray" size="2">
                {t('common.close')}
              </Button>
            </Dialog.Close>
          )}
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
