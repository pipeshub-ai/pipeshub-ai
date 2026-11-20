'use client';

import { useEffect, useRef, useState } from 'react';
import {
  Dialog,
  Flex,
  Text,
  Button,
  TextArea,
  Spinner,
  Box,
  IconButton,
} from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { toast } from '@/lib/store/toast-store';
import { FeedbackApi } from '../api';
import {
  ALLOWED_ATTACHMENT_EXTENSIONS,
  ATTACHMENT_ACCEPT,
  fileExtension,
  MAX_ATTACHMENT_BYTES,
  MAX_ATTACHMENTS,
  MAX_ATTACHMENTS_TOTAL_BYTES,
} from '../attachment-rules';
import { useFeedbackDialogStore } from '../store';
import { useSupportDialogStore } from '../../support/store';
import { SupportApi } from '../../support/api';
import { EmailAppView, type MailDraft } from '../../support/components/result-step';
import { SmtpNotice } from '../../support/components/smtp-notice';
import type { SupportContext } from '../../support/types';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { MAIL_DETAILS_SEPARATOR } from '../../support/utils';
import type { FeedbackKind } from '../types';

const MIN_DESCRIPTION = 10;
const MAX_DESCRIPTION = 5000;

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function fileIcon(name: string): string {
  const ext = fileExtension(name);
  if (['jpg', 'jpeg', 'png', 'webp', 'gif'].includes(ext)) return 'image';
  if (ext === 'pdf') return 'picture_as_pdf';
  if (ext === 'csv') return 'table_chart';
  return 'description';
}

function KindChip({
  selected,
  icon,
  label,
  disabled,
  onSelect,
}: {
  selected: boolean;
  icon: string;
  label: string;
  disabled: boolean;
  onSelect: () => void;
}) {
  const [hovered, setHovered] = useState(false);

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onSelect}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        appearance: 'none',
        font: 'inherit',
        cursor: disabled ? 'default' : 'pointer',
        // Grow to share the row; drop to its own row instead of wrapping its label.
        flex: '1 1 auto',
        whiteSpace: 'nowrap',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '10px 12px',
        borderRadius: 'var(--radius-3)',
        border: selected
          ? '1px solid var(--accent-7)'
          : '1px solid var(--olive-4)',
        backgroundColor: selected
          ? 'var(--accent-a3)'
          : hovered
            ? 'var(--olive-3)'
            : 'var(--olive-2)',
        color: selected ? 'var(--accent-11)' : 'var(--slate-12)',
        transition: 'background-color 140ms ease, border-color 140ms ease',
      }}
    >
      <MaterialIcon
        name={icon}
        size={18}
        color={selected ? 'var(--accent-11)' : 'var(--slate-11)'}
      />
      <Text size="2" weight={selected ? 'medium' : 'regular'}>
        {label}
      </Text>
    </button>
  );
}

export function SendFeedbackDialog() {
  const { t } = useTranslation();
  const isOpen = useFeedbackDialogStore((s) => s.isOpen);
  const close = useFeedbackDialogStore((s) => s.close);
  const openSupport = useSupportDialogStore((s) => s.open);
  const inputRef = useRef<HTMLInputElement>(null);

  const [kind, setKind] = useState<FeedbackKind>('issue');
  const [description, setDescription] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | undefined>();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [mail, setMail] = useState<MailDraft | null>(null);
  const [context, setContext] = useState<SupportContext | null>(null);
  const isAdmin = useUserStore(selectIsAdmin) === true;

  // Learn whether email is set up, so the form can say so before the person writes anything.
  useEffect(() => {
    if (!isOpen) return;
    const controller = new AbortController();
    SupportApi.getContext(controller.signal)
      .then(setContext)
      .catch(() => setContext(null));
    return () => controller.abort();
  }, [isOpen]);

  const reset = () => {
    setKind('issue');
    setDescription('');
    setFiles([]);
    setError(undefined);
    setIsSubmitting(false);
    setIsDragging(false);
    setMail(null);
  };

  const handleClose = () => {
    if (isSubmitting) return;
    close();
    reset();
  };

  const addFiles = (incoming: FileList | File[] | null) => {
    if (!incoming) return;
    const next = [...files];
    for (const file of Array.from(incoming)) {
      const ext = fileExtension(file.name);
      if (!ALLOWED_ATTACHMENT_EXTENSIONS.has(ext)) {
        setError(t('feedback.invalidType'));
        continue;
      }
      if (file.size > MAX_ATTACHMENT_BYTES) {
        setError(t('feedback.fileTooLarge'));
        continue;
      }
      if (next.length >= MAX_ATTACHMENTS) {
        setError(t('feedback.tooManyFiles'));
        break;
      }
      if (next.reduce((sum, kept) => sum + kept.size, file.size) > MAX_ATTACHMENTS_TOTAL_BYTES) {
        setError(t('feedback.filesTotalTooLarge'));
        continue;
      }
      next.push(file);
    }
    setFiles(next);
    if (inputRef.current) {
      inputRef.current.value = '';
    }
  };

  // Without SMTP (or when the send fails) the person sends it from their own mail app, as Contact support does.
  const showEmailInstead = async (text: string) => {
    const known = context ?? (await SupportApi.getContext().catch(() => null));
    const label = kind === 'issue' ? t('feedback.kindIssue') : t('feedback.kindFeedback');
    const workspace = known?.workspaceName ? ` ${known.workspaceName}` : '';
    setMail({
      to: known?.supportEmail ?? '',
      subject: `[${t('feedback.title')} · ${label}]${workspace}`,
      body: `${text}${MAIL_DETAILS_SEPARATOR}${label}`,
    });
  };

  const handleSubmit = async () => {
    const trimmed = description.trim();
    if (trimmed.length < MIN_DESCRIPTION) {
      setError(t('feedback.descriptionTooShort'));
      return;
    }

    setIsSubmitting(true);
    setError(undefined);
    try {
      if (context && !context.smtpConfigured) {
        await showEmailInstead(trimmed);
        return;
      }
      const { outcome } = await FeedbackApi.submit({ kind, description: trimmed, files });
      // Nothing is stored, so feedback that did not go out is lost unless the person sends it again.
      if (outcome !== 'sent' && outcome !== 'pending') {
        await showEmailInstead(trimmed);
        return;
      }
      toast.success(t('feedback.success'));
      close();
      reset();
    } catch {
      await showEmailInstead(trimmed);
    } finally {
      setIsSubmitting(false);
    }
  };

  const trimmedLength = description.trim().length;
  const canSubmit = trimmedLength >= MIN_DESCRIPTION && !isSubmitting;

  return (
    <Dialog.Root
      open={isOpen}
      onOpenChange={(next) => {
        if (!next) handleClose();
      }}
    >
      <Dialog.Content
        align="center"
        maxWidth="30rem"
        aria-describedby={undefined}
        style={{
          padding: 0,
          maxHeight: 'min(680px, calc(100vh - 40px))',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 16,
          border: '1px solid var(--olive-a4)',
          boxShadow: '0 16px 48px rgba(0, 0, 0, 0.28)',
          overflow: 'hidden',
        }}
      >
        <Flex
          align="center"
          justify="between"
          style={{
            flexShrink: 0,
            padding: '16px 16px 12px',
          }}
        >
          <Flex align="center" gap="3" minWidth="0">
            <Flex
              align="center"
              justify="center"
              style={{
                width: 32,
                height: 32,
                borderRadius: 10,
                backgroundColor: 'var(--accent-a3)',
                flexShrink: 0,
              }}
            >
              <MaterialIcon name="feedback" size={18} color="var(--accent-11)" />
            </Flex>
            <Flex direction="column" minWidth="0">
              <Dialog.Title size="3" weight="medium" mb="0">
                {t('feedback.title')}
              </Dialog.Title>
              <Text size="1" color="gray">
                {t('feedback.subtitle')}
              </Text>
            </Flex>
          </Flex>
          <IconButton
            type="button"
            variant="ghost"
            color="gray"
            size="2"
            disabled={isSubmitting}
            aria-label={t('feedback.cancel')}
            onClick={handleClose}
          >
            <MaterialIcon name="close" size={18} />
          </IconButton>
        </Flex>

        {mail ? (
          <Box style={{ flex: 1, minHeight: 0, overflowY: 'auto', paddingBottom: 16 }}>
            <EmailAppView mail={mail} fileCount={files.length} onDone={handleClose} />
          </Box>
        ) : (
        <>
        <Box
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            padding: '4px 16px 16px',
          }}
        >
          <Flex direction="column" gap="3">
            {context && !context.smtpConfigured && (
              <SmtpNotice supportEmail={context.supportEmail} isAdmin={isAdmin} />
            )}

            <Flex gap="2" wrap="wrap">
              <KindChip
                selected={kind === 'issue'}
                icon="chat_bubble"
                label={t('feedback.kindIssue')}
                disabled={isSubmitting}
                onSelect={() => setKind('issue')}
              />
              <KindChip
                selected={kind === 'feedback'}
                icon="lightbulb"
                label={t('feedback.kindFeedback')}
                disabled={isSubmitting}
                onSelect={() => setKind('feedback')}
              />
            </Flex>

            <Box style={{ position: 'relative' }}>
              <TextArea
                value={description}
                onChange={(event) => {
                  setDescription(event.target.value.slice(0, MAX_DESCRIPTION));
                  if (error) setError(undefined);
                }}
                placeholder={t('feedback.descriptionPlaceholder')}
                aria-label={t('feedback.descriptionPlaceholder')}
                disabled={isSubmitting}
                rows={7}
                style={{ paddingBottom: 28 }}
              />
              <Text
                size="1"
                color="gray"
                style={{
                  position: 'absolute',
                  right: 10,
                  bottom: 8,
                  pointerEvents: 'none',
                }}
              >
                {description.length}/{MAX_DESCRIPTION}
              </Text>
            </Box>

            <input
              ref={inputRef}
              type="file"
              accept={ATTACHMENT_ACCEPT}
              multiple
              hidden
              onChange={(event) => addFiles(event.target.files)}
            />

            <Box
              asChild
              style={{
                padding: '14px 12px',
                borderRadius: 'var(--radius-3)',
                border: `1px dashed ${isDragging ? 'var(--accent-7)' : 'var(--olive-5)'}`,
                backgroundColor: isDragging ? 'var(--accent-a2)' : 'var(--olive-2)',
                cursor: isSubmitting || files.length >= MAX_ATTACHMENTS ? 'default' : 'pointer',
                transition: 'background-color 140ms ease, border-color 140ms ease',
              }}
            >
              <button
                type="button"
                disabled={isSubmitting || files.length >= MAX_ATTACHMENTS}
                aria-label={t('feedback.addFiles')}
                onDragOver={(event) => {
                  event.preventDefault();
                  if (!isSubmitting && files.length < MAX_ATTACHMENTS) {
                    setIsDragging(true);
                  }
                }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setIsDragging(false);
                  if (!isSubmitting) {
                    addFiles(event.dataTransfer.files);
                  }
                }}
                onClick={() => {
                  if (!isSubmitting && files.length < MAX_ATTACHMENTS) {
                    inputRef.current?.click();
                  }
                }}
                style={{
                  appearance: 'none',
                  font: 'inherit',
                  width: '100%',
                  background: 'transparent',
                  color: 'inherit',
                }}
              >
              <Flex direction="column" align="center" gap="1">
                <MaterialIcon
                  name="attach_file"
                  size={18}
                  color={isDragging ? 'var(--accent-11)' : 'var(--slate-11)'}
                />
                <Text size="2" weight="medium">
                  {t('feedback.addFiles')}
                </Text>
                <Text size="1" color="gray" align="center">
                  {t('feedback.fileHint')}
                </Text>
              </Flex>
              </button>
            </Box>

            {files.length > 0 && (
              <Flex direction="column" gap="2">
                {files.map((file, index) => (
                  <Flex
                    key={`${file.name}-${index}`}
                    align="center"
                    gap="2"
                    style={{
                      padding: '8px 10px',
                      borderRadius: 'var(--radius-3)',
                      backgroundColor: 'var(--olive-3)',
                    }}
                  >
                    <MaterialIcon name={fileIcon(file.name)} size={18} color="var(--slate-11)" />
                    <Box minWidth="0" style={{ flex: 1 }}>
                      <Text
                        size="2"
                        style={{
                          display: 'block',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {file.name}
                      </Text>
                      <Text size="1" color="gray">
                        {formatBytes(file.size)}
                      </Text>
                    </Box>
                    <IconButton
                      type="button"
                      variant="ghost"
                      color="gray"
                      size="1"
                      disabled={isSubmitting}
                      aria-label={t('feedback.removeFile')}
                      onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}
                    >
                      <MaterialIcon name="close" size={14} />
                    </IconButton>
                  </Flex>
                ))}
              </Flex>
            )}

            {error && (
              <Flex
                align="center"
                gap="2"
                style={{
                  padding: '8px 10px',
                  borderRadius: 'var(--radius-3)',
                  backgroundColor: 'var(--red-a3)',
                }}
              >
                <MaterialIcon name="error_outline" size={16} color="var(--red-11)" />
                <Text size="2" style={{ color: 'var(--red-11)' }}>
                  {error}
                </Text>
              </Flex>
            )}
          </Flex>
        </Box>

        {/* Same footer as Contact support: help link left, Cancel + Submit right; phones stack, primary on top. */}
        <Flex
          direction={{ initial: 'column-reverse', sm: 'row' }}
          align={{ initial: 'stretch', sm: 'center' }}
          justify="between"
          gap="3"
          style={{ flexShrink: 0, padding: '0 16px 16px' }}
        >
          <Flex justify={{ initial: 'center', sm: 'start' }}>
            <Button
              variant="ghost"
              size="2"
              disabled={isSubmitting}
              onClick={() => {
                handleClose();
                openSupport();
              }}
            >
              <MaterialIcon name="support_agent" size={16} />
              {t('feedback.needReply')}
            </Button>
          </Flex>
          <Flex direction={{ initial: 'column-reverse', sm: 'row' }} gap="2">
            <Button variant="outline" color="gray" size="2" disabled={isSubmitting} onClick={handleClose}>
              {t('feedback.cancel')}
            </Button>
            <Button size="2" onClick={handleSubmit} disabled={!canSubmit}>
              {isSubmitting ? <Spinner size="2" /> : context && !context.smtpConfigured ? t('support.continue') : t('feedback.submit')}
            </Button>
          </Flex>
        </Flex>
        </>
        )}
      </Dialog.Content>
    </Dialog.Root>
  );
}
