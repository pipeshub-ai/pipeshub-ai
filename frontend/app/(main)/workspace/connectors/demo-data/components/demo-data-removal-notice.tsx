'use client';

import React, { useState } from 'react';
import { Button, Callout, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useDemoDataActive, useDemoDataStatus, useDemoRemovalNotice } from '../use-demo-data';
import { useDemoSwitch } from '../use-demo-switch';
import { RemoveDemoDataDialog } from './remove-demo-data-dialog';

interface DemoDataRemovalNoticeProps {
  isAdmin: boolean | null;
  /** Applied to the notice only, so no space is left behind while it is hidden. */
  style?: React.CSSProperties;
}

/**
 * Tells an admin that answers still include the Acme Corp demo data once the
 * company's own data has arrived. Offers to turn it off for everyone (undoable)
 * or remove it permanently. Nothing is removed without asking; "Keep for now"
 * hides the notice for a week in this browser.
 */
export function DemoDataRemovalNotice({ isAdmin, style }: DemoDataRemovalNoticeProps) {
  const { t } = useTranslation();
  useDemoDataActive();
  const { show: showRemoval, demoConnectors, snooze } = useDemoRemovalNotice(isAdmin);
  const offForEveryone = useDemoDataStatus()?.offForEveryone === true;
  const { setEnabledForEveryone, busy } = useDemoSwitch();
  const [dialogOpen, setDialogOpen] = useState(false);
  // Already off for everyone: nothing is mixing into answers any more.
  const show = showRemoval && !offForEveryone;

  if (!show && !dialogOpen) return null;

  const connectorIds = demoConnectors.map((c) => c._key).filter((id): id is string => !!id);

  return (
    <>
      {show && (
        <Callout.Root color="orange" variant="surface" size="1" style={{ width: '100%', ...style }}>
          <Callout.Icon>
            <MaterialIcon name="info" size={16} />
          </Callout.Icon>
          <Flex direction="column" gap="2">
            <Text size="2" weight="medium">
              {t('demoData.removalNotice.title')}
            </Text>
            <Text size="2">{t('demoData.removalNotice.description')}</Text>
            <Flex gap="2" wrap="wrap">
              <Button size="1" color="orange" disabled={busy} onClick={() => void setEnabledForEveryone(false)}>
                {t('demoData.removalNotice.turnOff')}
              </Button>
              <Button size="1" variant="soft" color="red" onClick={() => setDialogOpen(true)}>
                {t('demoData.removalNotice.remove')}
              </Button>
              <Button size="1" variant="soft" color="gray" onClick={snooze}>
                {t('demoData.removalNotice.keep')}
              </Button>
            </Flex>
          </Flex>
        </Callout.Root>
      )}
      <RemoveDemoDataDialog open={dialogOpen} onOpenChange={setDialogOpen} connectorIds={connectorIds} />
    </>
  );
}
