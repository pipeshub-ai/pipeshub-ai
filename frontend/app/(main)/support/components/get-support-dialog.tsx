'use client';

import { Fragment, useCallback, useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { Box, Button, Dialog, Flex, IconButton, Spinner, Text, Tooltip, VisuallyHidden } from '@radix-ui/themes';
import { LoadingButton } from '@/app/components/ui/loading-button';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { isProcessedError } from '@/lib/api';
import { selectIsAdmin, useUserStore } from '@/lib/store/user-store';
import { getRecentErrors, installRecentErrorRecorder, type RecentError } from '@/lib/support/recent-errors';
import { useFeedbackDialogStore } from '../../feedback/store';
import { SupportApi } from '../api';
import { areaForPath } from '../areas';
import { useSupportDialogStore } from '../store';
import type { SupportAreaKey, SupportContext, SupportDraft, SupportRow } from '../types';
import { MAIL_DETAILS_SEPARATOR, describeBrowser, newRequestKey } from '../utils';
import { AreaStep } from './area-step';
import { DetailsStep, MIN_DESCRIPTION } from './details-step';
import { EmailAppView, needsEmailApp, SavedView, type FinishedRequest, type MailDraft } from './result-step';

type LoadState = 'loading' | 'ready' | 'error';
type Step = 'area' | 'details';

const BLOCKED_REASON_ID = 'support-blocked-reason';

// Rows the server sends in English; shown in the user's language.
const ROW_KEYS: Record<string, { label: string; sentPart: string }> = {
  Workspace: { label: 'support.details.rows.workspace', sentPart: 'support.details.sentParts.workspace' },
  'Signed in as': { label: 'support.details.rows.signedInAs', sentPart: 'support.details.sentParts.identity' },
};

function emptyDraft(): SupportDraft {
  return { area: null, description: '', files: [], includeErrors: true, requestKey: newRequestKey() };
}

function issueLabel(t: TFunction, area: SupportAreaKey): string {
  return area === 'other' ? t('support.issueOther') : t(`support.areas.${area}.label`);
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function StepIndicator({ current }: { current: 1 | 2 }) {
  const { t } = useTranslation();
  const steps = [t('support.steps.area'), t('support.steps.details')];

  return (
    <Flex asChild align="center" gap="2">
      <ol style={{ margin: 0, padding: 0, listStyle: 'none' }}>
        {steps.map((label, index) => {
          const number = index + 1;
          const isDone = number < current;
          const isCurrent = number === current;
          return (
            <Fragment key={label}>
              {index > 0 && <li aria-hidden="true" style={{ width: 32, height: 1, background: 'var(--gray-a6)' }} />}
              <Flex asChild align="center" gap="2">
                <li aria-current={isCurrent ? 'step' : undefined}>
                  <Flex
                    aria-hidden="true"
                    align="center"
                    justify="center"
                    flexShrink="0"
                    style={{
                      width: 20,
                      height: 20,
                      boxSizing: 'border-box',
                      borderRadius: '50%',
                      fontSize: 12,
                      fontWeight: 700,
                      border: isDone || isCurrent ? 'none' : '1px solid var(--gray-a7)',
                      background: isCurrent ? 'var(--accent-9)' : isDone ? 'var(--accent-a3)' : 'transparent',
                      color: isCurrent ? 'var(--accent-contrast)' : isDone ? 'var(--accent-11)' : 'var(--slate-11)',
                    }}
                  >
                    {isDone ? <MaterialIcon name="check" size={14} /> : number}
                  </Flex>
                  <Text
                    weight={isCurrent ? 'bold' : 'medium'}
                    style={{ fontSize: 13, lineHeight: '20px', color: isCurrent ? 'var(--slate-12)' : 'var(--slate-11)' }}
                  >
                    {label}
                  </Text>
                </li>
              </Flex>
            </Fragment>
          );
        })}
      </ol>
    </Flex>
  );
}

export function GetSupportDialog() {
  const openCount = useSupportDialogStore((s) => s.openCount);
  // During render, not in an effect: calls that other components start from
  // their own effects in this same commit have to be watched already.
  useState(installRecentErrorRecorder);

  // A new key per open: nothing typed, picked or attached survives a close.
  return <SupportDialog key={openCount} />;
}

function SupportDialog() {
  const { t, i18n } = useTranslation();
  const isOpen = useSupportDialogStore((s) => s.isOpen);
  const close = useSupportDialogStore((s) => s.close);
  const isAdmin = useUserStore(selectIsAdmin) === true;
  const openFeedback = useFeedbackDialogStore((s) => s.open);

  const [draft, setDraft] = useState(emptyDraft);
  const [recentErrors] = useState(getRecentErrors);
  const [context, setContext] = useState<SupportContext | null>(null);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [reloadKey, setReloadKey] = useState(0);
  const [step, setStep] = useState<Step>('area');
  const [submitting, setSubmitting] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [canEmailInstead, setCanEmailInstead] = useState(false);
  const [finished, setFinished] = useState<FinishedRequest | null>(null);

  const updateDraft = useCallback(
    (patch: Partial<SupportDraft>) => setDraft((current) => ({ ...current, ...patch })),
    [],
  );

  useEffect(() => {
    if (!isOpen) return;
    const controller = new AbortController();
    setLoadState('loading');
    SupportApi.getContext(controller.signal)
      .then((loaded) => {
        setContext(loaded);
        setLoadState('ready');
      })
      .catch(() => {
        if (!controller.signal.aborted) setLoadState('error');
      });
    return () => controller.abort();
  }, [isOpen, reloadKey]);

  const area = draft.area;
  const lastError = recentErrors.at(-1);
  const suggested = lastError ? areaForPath(lastError.path) : null;

  const describeError = (error: RecentError): string => {
    const errorArea = areaForPath(error.path);
    const values = { status: error.status, time: formatTime(error.at) };
    return errorArea
      ? t('support.details.errorLine', { ...values, area: t(`support.areas.${errorArea}.label`) })
      : t('support.details.errorLineUnknown', values);
  };

  const browser = typeof navigator === 'undefined' ? '' : describeBrowser(navigator.userAgent);

  const detailRows: SupportRow[] = area
    ? [
        { label: t('support.details.rows.issue'), value: issueLabel(t, area) },
        ...(context?.details ?? []).map((row) => ({
          label: ROW_KEYS[row.label] ? t(ROW_KEYS[row.label].label) : row.label,
          value: row.value,
        })),
        { label: t('support.details.rows.browser'), value: browser },
      ]
    : [];

  const sentParts = [
    ...(context?.details ?? []).map((row) => (ROW_KEYS[row.label] ? t(ROW_KEYS[row.label].sentPart) : row.label)),
    t('support.details.sentParts.browser'),
    ...(draft.includeErrors && recentErrors.length > 0 ? [t('support.details.sentParts.errors')] : []),
  ];
  const sentSummary = t('support.details.sentWith', {
    list: new Intl.ListFormat(i18n.language, { type: 'conjunction' }).format(sentParts),
  });

  const blockedReason = (): string | null => {
    if (step === 'area') return area ? null : t('support.blocked.area');
    const left = MIN_DESCRIPTION - draft.description.trim().length;
    return left > 0 ? t('support.blocked.description', { min: MIN_DESCRIPTION, left }) : null;
  };
  const blocked = loadState === 'ready' && !finished ? blockedReason() : null;

  // What a stray click outside would lose: typed text, or the ready-made email when nothing has reached support.
  const hasUnsentText = finished
    ? needsEmailApp(finished)
    : draft.description.trim().length > 0 || draft.files.length > 0;

  const buildMail = (reference: string | null): MailDraft => {
    const issue = area ? issueLabel(t, area) : '';
    const workspace = context?.workspaceName ?? '';
    const details = [
      ...(reference ? [`${t('support.result.referenceLabel')}: ${reference}`] : []),
      `${t('support.details.rows.issue')}: ${issue}`,
      ...(context?.details ?? []).map((row) => `${row.label}: ${row.value}`),
      `${t('support.details.rows.browser')}: ${browser}`,
      ...(draft.includeErrors
        ? recentErrors.map((error) => `${error.method} ${error.path} · ${error.status} · ${error.requestId ?? '-'} · ${error.at}`)
        : []),
    ];
    return {
      to: context?.supportEmail ?? '',
      subject: `[Support · ${issue}]${workspace ? ` ${workspace}` : ''}${reference ? ` · ${reference}` : ''}`,
      body: `${draft.description.trim()}${MAIL_DETAILS_SEPARATOR}${details.join('\n')}`,
    };
  };

  const send = async () => {
    if (!context || !area || blocked || submitting) return;
    setSubmitting(true);
    setSendError(null);
    setCanEmailInstead(false);
    try {
      const result = await SupportApi.submit({
        area,
        description: draft.description.trim(),
        requestKey: draft.requestKey,
        recentErrors: draft.includeErrors ? recentErrors : [],
        files: draft.files,
      });
      setFinished({ kind: 'saved', result, mail: buildMail(result.reference) });
    } catch (error) {
      const status = isProcessedError(error) ? error.statusCode : undefined;
      if (status === 429) {
        setFinished({ kind: 'limit', mail: buildMail(null) });
      } else if (status === 400 && isProcessedError(error)) {
        setSendError(error.message);
      } else {
        // The server could not take it at all: the person can still reach support by email.
        setSendError(t('support.errors.send'));
        setCanEmailInstead(true);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const handleOpenChange = (next: boolean) => {
    if (!next && !submitting) close();
  };

  const header = (title: string, options: { description?: string; step?: 1 | 2 } = {}) => (
    <Flex direction="column" gap="3" px="5">
      <Flex justify="between" align="center" gap="3">
        <Dialog.Title size="4" weight="bold" mb="0" style={{ minWidth: 0, color: 'var(--slate-12)' }}>
          {title}
        </Dialog.Title>
        <IconButton variant="ghost" color="gray" aria-label={t('support.close')} onClick={close} disabled={submitting}>
          <MaterialIcon name="close" size={18} />
        </IconButton>
      </Flex>
      {options.step !== undefined && <StepIndicator current={options.step} />}
      {options.description && (
        <Dialog.Description size="2" style={{ color: 'var(--slate-11)', lineHeight: '20px' }}>
          {options.description}
        </Dialog.Description>
      )}
    </Flex>
  );

  const cancelButton = (
    <Button variant="outline" color="gray" size="2" onClick={close} disabled={submitting}>
      {t('support.cancel')}
    </Button>
  );

  const primaryButton = (label: string, onClick: () => void) => {
    const button = (
      <LoadingButton
        size="2"
        onClick={onClick}
        disabled={blocked !== null}
        loading={submitting}
        loadingLabel={t('support.sending')}
        aria-describedby={blocked ? BLOCKED_REASON_ID : undefined}
        // A disabled button gets no hover events; let the wrapper show the tooltip.
        style={{ flexGrow: 1, ...(blocked ? { pointerEvents: 'none' } : {}) }}
      >
        {label}
      </LoadingButton>
    );
    return blocked ? (
      <Tooltip content={blocked}>
        <span style={{ display: 'flex' }}>
          {button}
          <VisuallyHidden id={BLOCKED_REASON_ID}>{blocked}</VisuallyHidden>
        </span>
      </Tooltip>
    ) : (
      button
    );
  };

  let body: ReactNode;
  let footerStart: ReactNode = null;
  let footerEnd: ReactNode = null;
  let described = true;

  if (loadState === 'loading') {
    described = false;
    body = (
      <>
        {header(t('support.title'))}
        <Flex justify="center" py="6">
          <Spinner size="3" />
        </Flex>
      </>
    );
    footerEnd = cancelButton;
  } else if (loadState === 'error' || !context) {
    described = false;
    body = (
      <>
        {header(t('support.title'))}
        <Box px="5">
          <Text as="p" size="2" role="alert" style={{ margin: 0, color: 'var(--red-11)' }}>
            {t('support.errors.load')}
          </Text>
        </Box>
      </>
    );
    footerEnd = (
      <>
        {cancelButton}
        <Button size="2" onClick={() => setReloadKey((key) => key + 1)}>
          {t('support.errors.retry')}
        </Button>
      </>
    );
  } else if (finished && !needsEmailApp(finished) && finished.kind === 'saved') {
    described = false;
    body = (
      <>
        <VisuallyHidden>
          <Dialog.Title>
            {finished.result.outcome === 'pending' ? t('support.result.pendingTitle') : t('support.result.sentTitle')}
          </Dialog.Title>
        </VisuallyHidden>
        <SavedView result={finished.result} />
      </>
    );
    footerEnd = (
      <Button size="2" onClick={close}>
        {t('support.result.done')}
      </Button>
    );
  } else if (finished) {
    const reason =
      finished.kind === 'limit'
        ? t('support.email.limit')
        : finished.kind === 'unsaved'
          ? t('support.email.unsaved')
          : finished.result.outcome === 'no_smtp'
            ? t('support.email.noSmtp', { reference: finished.result.reference })
            : t('support.email.failed', { reference: finished.result.reference });
    body = (
      <>
        {header(t('support.email.title'), { description: reason })}
        <EmailAppView mail={finished.mail} fileCount={draft.files.length} onDone={close} />
      </>
    );
  } else if (step === 'area' || !area) {
    body = (
      <>
        {header(t('support.title'), { step: 1 })}
        <AreaStep
          selected={area}
          suggested={suggested}
          suggestionTime={lastError ? formatTime(lastError.at) : null}
          onSelect={(key) => updateDraft({ area: key })}
        />
      </>
    );
    // Feedback is emailed too and refuses to open without SMTP, so the link would close this dialog for nothing.
    footerStart = context.smtpConfigured ? (
      <Button
        variant="ghost"
        size="2"
        onClick={() => {
          close();
          openFeedback();
        }}
      >
        <MaterialIcon name="lightbulb" size={16} />
        {t('support.areaStep.idea')}
      </Button>
    ) : null;
    footerEnd = (
      <>
        {cancelButton}
        {primaryButton(t('support.continue'), () => setStep('details'))}
      </>
    );
  } else {
    described = false;
    body = (
      <>
        {header(t('support.title'), { step: 2 })}
        <DetailsStep
          area={area}
          draft={draft}
          onChange={updateDraft}
          onChangeArea={() => setStep('area')}
          isAdmin={isAdmin}
          smtpConfigured={context.smtpConfigured}
          supportEmail={context.supportEmail}
          disabled={submitting}
          detailRows={detailRows}
          sentSummary={sentSummary}
          recentErrors={recentErrors}
          describeError={describeError}
        />
        {sendError && (
          <Flex direction="column" align="start" gap="2" px="5">
            <Text as="p" size="2" role="alert" style={{ margin: 0, color: 'var(--red-11)' }}>
              {sendError}
            </Text>
            {canEmailInstead && (
              <Button
                type="button"
                variant="ghost"
                size="2"
                onClick={() => setFinished({ kind: 'unsaved', mail: buildMail(null) })}
              >
                <MaterialIcon name="mail" size={16} />
                {t('support.errors.emailInstead')}
              </Button>
            )}
          </Flex>
        )}
      </>
    );
    footerStart = (
      <Button
        variant="outline"
        color="gray"
        size="2"
        onClick={() => setStep('area')}
        disabled={submitting}
        style={{ flexGrow: 1 }}
      >
        {t('support.back')}
      </Button>
    );
    footerEnd = (
      <>
        {cancelButton}
        {primaryButton(context.smtpConfigured ? t('support.send') : t('support.continue'), () => void send())}
      </>
    );
  }

  return (
    <Dialog.Root open={isOpen} onOpenChange={handleOpenChange}>
      <Dialog.Content
        align="start"
        maxWidth="37.5rem"
        {...(described ? {} : { 'aria-describedby': undefined })}
        // Nothing is kept after closing, so a stray click outside must not throw away what was typed.
        onPointerDownOutside={(event) => {
          if (hasUnsentText) event.preventDefault();
        }}
        style={{
          padding: 'var(--space-5) 0',
          borderRadius: 'var(--radius-5)',
          border: '1px solid var(--olive-a3)',
          boxShadow:
            '0 16px 36px -20px rgba(0, 6, 46, 0.2), 0 16px 64px rgba(0, 0, 85, 0.02), 0 12px 60px rgba(0, 0, 0, 0.15)',
        }}
      >
        <Flex direction="column" style={{ gap: 20 }}>
          {body}
          {footerEnd && (
            // Phones stack full-width buttons with the primary action on top.
            <Flex
              direction={{ initial: 'column-reverse', sm: 'row' }}
              align={{ initial: 'stretch', sm: 'center' }}
              justify={footerStart ? 'between' : 'end'}
              gap="3"
              px="5"
            >
              {footerStart && <Flex justify={{ initial: 'center', sm: 'start' }}>{footerStart}</Flex>}
              <Flex direction={{ initial: 'column-reverse', sm: 'row' }} gap="2">
                {footerEnd}
              </Flex>
            </Flex>
          )}
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
