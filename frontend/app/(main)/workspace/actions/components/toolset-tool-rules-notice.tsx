'use client';

import { useTranslation } from 'react-i18next';
import { Button, Dialog, Flex, Text } from '@radix-ui/themes';
import { EnterpriseFeatureNotice } from '@/app/components/ui/enterprise-badge';

interface ToolsetToolRulesNoticeProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  instanceId: string;
  instanceName: string;
  toolsetType: string;
  personal?: boolean;
  /** Portal host, to stack above a workspace drawer (`useWorkspaceDrawerNestedModalHost`). */
  container?: HTMLElement | null;
}

/** "Tool approvals" in this edition: what a toolset's tools do by default, and where organization
 * rules are available. Same props as the rules editor, so either can sit behind the button. */
export function ToolsetToolRulesNotice({ open, onOpenChange, instanceName, container }: ToolsetToolRulesNoticeProps) {
  const { t } = useTranslation();

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content maxWidth="520px" container={container ?? undefined}>
        <Dialog.Title>{t('workspace.actions.toolRules.title', { name: instanceName })}</Dialog.Title>
        <Flex direction="column" gap="3">
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.actions.toolRules.defaults')}
          </Text>
          <EnterpriseFeatureNotice
            title={t('workspace.actions.toolRules.enterpriseTitle')}
            description={t('workspace.actions.toolRules.enterpriseDescription')}
          />
        </Flex>
        <Flex justify="end" mt="4">
          <Dialog.Close>
            <Button variant="soft" color="gray">
              {t('common.close')}
            </Button>
          </Dialog.Close>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
