'use client';

import { useCallback, useEffect, useState } from 'react';
import { Box, Button, Dialog, Flex, Select, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ServiceAccountsApi } from '../../../workspace/service-accounts/api';

/** The Select cannot hold an empty string, so "no binding" needs a token. */
const CREATOR = '__creator__';

interface ServiceAccountOption {
  id: string;
  fullName: string;
  email: string;
  /** Only ever true for an account this agent is already bound to. */
  isDisabled?: boolean;
}

export interface ServiceAgentIdentityControlProps {
  /** The bound account's `userId`, or null when the agent searches as its creator. */
  value: string | null;
  onChange: (next: string | null) => void;
  /** The bound account, when the list has not been fetched yet. */
  boundLabel?: string | null;
  disabled?: boolean;
  /** Admin-only, like every other service-account choice. */
  permissionDenied?: boolean;
}

/**
 * Chooses the identity a service agent searches knowledge as.
 *
 * A service agent is shared with the whole organisation, so this is the one
 * setting that decides what everybody's questions can reach. Left alone it
 * searches as the person who made it — the older behaviour, kept because
 * ending it silently would cut off agents that work today. Binding it to a
 * service account replaces that with an identity an administrator grants
 * deliberately, usually through group membership.
 *
 * Shown as what it currently is rather than as a blank control, because the
 * answer matters more than the act of changing it.
 */
export function ServiceAgentIdentityControl({
  value,
  onChange,
  boundLabel,
  disabled,
  permissionDenied,
}: ServiceAgentIdentityControlProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [accounts, setAccounts] = useState<ServiceAccountOption[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string>(value ?? CREATOR);

  useEffect(() => {
    // Also when the agent is already bound and the dialog is shut: the closed
    // button names the account, and without the list it could only say "a
    // service account", which is the one thing an administrator already knows.
    if (!open && value === null) return;
    if (accounts !== null || loadError !== null) return;

    let cancelled = false;
    void (async () => {
      try {
        const { serviceAccounts } = await ServiceAccountsApi.list();
        if (cancelled) return;
        setAccounts(
          serviceAccounts
            // A disabled account is not offered. It would be refused anyway,
            // and offering it invites a binding that silently reads nothing.
            // One already bound is kept, so the button can still name it
            // rather than going vague about an agent that is bound right now.
            .filter((a) => !a.isDisabled || a.id === value)
            .map((a) => ({
              id: a.id,
              fullName: a.fullName,
              email: a.email,
              isDisabled: a.isDisabled,
            }))
        );
      } catch {
        if (cancelled) return;
        setLoadError(t('agentBuilder.identityLoadError'));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, value, accounts, loadError, t]);

  const openDialog = useCallback(() => {
    setChosen(value ?? CREATOR);
    setOpen(true);
  }, [value]);

  const confirm = useCallback(() => {
    onChange(chosen === CREATOR ? null : chosen);
    setOpen(false);
  }, [chosen, onChange]);

  const chosenAccount = accounts?.find((a) => a.id === value);
  const current =
    value === null
      ? t('agentBuilder.identityCreator')
      : (chosenAccount
          ? `${chosenAccount.fullName} (${chosenAccount.email})`
          : boundLabel) || t('agentBuilder.identityBoundUnknown');

  const trigger = (
    <Button
      type="button"
      variant="soft"
      color="gray"
      size="2"
      disabled={disabled || permissionDenied}
      onClick={openDialog}
      style={{ flexShrink: 0, maxWidth: 260 }}
    >
      <Flex align="center" gap="2" style={{ minWidth: 0 }}>
        <MaterialIcon name="badge" size={16} />
        <Text
          size="2"
          style={{
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {t('agentBuilder.identitySearchesAs', { identity: current })}
        </Text>
      </Flex>
    </Button>
  );

  return (
    <>
      <Tooltip content={t('agentBuilder.identityTooltip')}>{trigger}</Tooltip>

      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Content style={{ maxWidth: 520 }}>
          <Dialog.Title>{t('agentBuilder.identityDialogTitle')}</Dialog.Title>
          <Dialog.Description size="2" mb="4" color="gray">
            {t('agentBuilder.identityDialogBody')}
          </Dialog.Description>

          {loadError !== null ? (
            <Flex align="center" gap="2">
              <Text size="2" color="red">
                {loadError}
              </Text>
              <Button
                size="1"
                variant="soft"
                type="button"
                onClick={() => {
                  setLoadError(null);
                  setAccounts(null);
                }}
              >
                {t('agentBuilder.identityRetry')}
              </Button>
            </Flex>
          ) : accounts === null ? (
            <Text size="2" color="gray">
              {t('agentBuilder.identityLoading')}
            </Text>
          ) : (
            <Box>
              <Select.Root value={chosen} onValueChange={setChosen}>
                <Select.Trigger
                  style={{ width: '100%' }}
                  aria-label={t('agentBuilder.identityDialogTitle')}
                />
                <Select.Content>
                  <Select.Item value={CREATOR}>
                    {t('agentBuilder.identityCreator')}
                  </Select.Item>
                  {accounts
                    .filter((a) => !a.isDisabled)
                    .map((a) => (
                      <Select.Item key={a.id} value={a.id}>
                        {a.fullName} ({a.email})
                      </Select.Item>
                    ))}
                </Select.Content>
              </Select.Root>
              {accounts.length === 0 ? (
                <Text size="1" color="gray" as="p" mt="2">
                  {t('agentBuilder.identityNone')}
                </Text>
              ) : null}
            </Box>
          )}

          <Flex gap="2" mt="4" justify="end">
            <Dialog.Close>
              <Button variant="soft" color="gray" type="button">
                {t('common.cancel')}
              </Button>
            </Dialog.Close>
            <Button
              type="button"
              onClick={confirm}
              disabled={accounts === null && loadError === null}
            >
              {t('agentBuilder.identityChoose')}
            </Button>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
}
