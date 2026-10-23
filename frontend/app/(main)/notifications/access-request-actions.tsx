'use client';

import { useEffect, useState } from 'react';
import { AlertDialog, Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { apiClient } from '@/lib/api';

type Decision = 'approve' | 'deny';
type Status = 'pending' | 'approved' | 'denied' | 'forwarded';
type State = 'idle' | 'sending' | 'failed';

export const AccessRequestsApi = {
  async get(requestId: string): Promise<Status> {
    const { data } = await apiClient.get<{ request: { status: Status } }>(
      `/api/v1/access-requests/${requestId}`,
      { suppressErrorToast: true },
    );
    return data.request.status;
  },
  async decide(requestId: string, decision: Decision): Promise<void> {
    await apiClient.post(`/api/v1/access-requests/${requestId}/${decision}`, {}, { suppressErrorToast: true });
  },
};

interface Props {
  requestId: string;
  requesterName?: string;
  /** Set when the record belongs to a collection: approving opens the whole collection. */
  collectionName?: string;
}

/** Approve / Deny on an `accessRequest` notification; the owner decides without leaving the panel. */
export function AccessRequestActions({ requestId, requesterName, collectionName }: Props) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<Status>('pending');
  const [state, setState] = useState<State>('idle');
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  // Decided by someone else (or earlier): said so, rather than "you just decided".
  const [earlier, setEarlier] = useState(false);

  useEffect(() => {
    let cancelled = false;
    AccessRequestsApi.get(requestId)
      .then((s) => {
        if (cancelled) return;
        setStatus(s);
        setEarlier(s === 'approved' || s === 'denied');
      })
      // Unknown state: leave the buttons, the server refuses a repeat decision.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [requestId]);

  const decide = async (decision: Decision) => {
    setState('sending');
    setError(null);
    try {
      await AccessRequestsApi.decide(requestId, decision);
      setStatus(decision === 'approve' ? 'approved' : 'denied');
      setState('idle');
    } catch (e) {
      const message = (e as { message?: string })?.message;
      if (/already decided/i.test(message ?? '')) {
        setError(t('notifications.accessRequest.alreadyDecided'));
        AccessRequestsApi.get(requestId)
          .then((s) => {
            setStatus(s);
            setEarlier(true);
          })
          .catch(() => undefined);
      } else {
        setError(message || t('notifications.accessRequest.failed'));
      }
      setState('failed');
    }
  };

  if (status === 'approved' || status === 'denied') {
    return (
      <Text size="1" color="gray" data-testid="access-request-result">
        {status === 'approved'
          ? t(earlier ? 'notifications.accessRequest.alreadyApproved' : 'notifications.accessRequest.approved')
          : t(earlier ? 'notifications.accessRequest.alreadyDeclined' : 'notifications.accessRequest.denied')}
      </Text>
    );
  }

  const who = requesterName || t('notifications.accessRequest.someone');
  return (
    <Flex gap="2" wrap="wrap" align="center" mt="1" data-testid="access-request-actions">
      <AlertDialog.Root open={confirming} onOpenChange={setConfirming}>
        <AlertDialog.Trigger>
          <Button size="2" disabled={state === 'sending'} style={{ minHeight: 44 }}>
            {t('notifications.accessRequest.approve')}
          </Button>
        </AlertDialog.Trigger>
        <AlertDialog.Content maxWidth="440px">
          <AlertDialog.Title>{t('notifications.accessRequest.approveTitle')}</AlertDialog.Title>
          <AlertDialog.Description size="2">
            {collectionName
              ? t('notifications.accessRequest.approveBodyCollection', { user: who, name: collectionName })
              : t('notifications.accessRequest.approveBodyRecord', { user: who })}
          </AlertDialog.Description>
          <Flex gap="3" mt="4" justify="end">
            <AlertDialog.Cancel>
              <Button variant="soft" color="gray" style={{ minHeight: 44 }}>
                {t('notifications.accessRequest.cancel')}
              </Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action>
              <Button style={{ minHeight: 44 }} onClick={() => void decide('approve')}>
                {t('notifications.accessRequest.approve')}
              </Button>
            </AlertDialog.Action>
          </Flex>
        </AlertDialog.Content>
      </AlertDialog.Root>
      <Button
        size="2"
        variant="soft"
        color="gray"
        disabled={state === 'sending'}
        onClick={() => void decide('deny')}
        style={{ minHeight: 44 }}
      >
        {t('notifications.accessRequest.deny')}
      </Button>
      {state === 'failed' && error && (
        <Text size="1" color="red" role="alert">
          {error}
        </Text>
      )}
    </Flex>
  );
}
