'use client';

import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Theme,
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
import { useThemeAppearance } from '@/app/components/theme-provider';
import { toast } from '@/lib/store/toast-store';
import { FeedbackApi } from '../api';
import { useFeedbackDialogStore } from '../store';
import type { FeedbackKind } from '../types';

const ACCEPT =
  'image/jpeg,image/png,image/webp,image/gif,application/pdf,text/plain,text/csv,.jpg,.jpeg,.png,.webp,.gif,.pdf,.txt,.log,.csv';
const MAX_FILES = 5;
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const MIN_DESCRIPTION = 10;
const MAX_DESCRIPTION = 5000;
const ALLOWED_EXTENSIONS = new Set([
  'jpg',
  'jpeg',
  'png',
  'webp',
  'gif',
  'pdf',
  'txt',
  'log',
  'csv',
]);

function fileExtension(name: string): string {
  const parts = name.split('.');
  return parts.length > 1 ? parts[parts.length - 1].toLowerCase() : '';
}

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
        flex: 1,
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
  const { appearance } = useThemeAppearance();
  const inputRef = useRef<HTMLInputElement>(null);
  const [mounted, setMounted] = useState(false);

  const [kind, setKind] = useState<FeedbackKind>('issue');
  const [description, setDescription] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | undefined>();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const reset = () => {
    setKind('issue');
    setDescription('');
    setFiles([]);
    setError(undefined);
    setIsSubmitting(false);
    setIsDragging(false);
  };

  const handleClose = () => {
    if (isSubmitting) return;
    close();
    reset();
  };

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !isSubmitting) {
        close();
        reset();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isOpen, isSubmitting, close]);

  const addFiles = (incoming: FileList | File[] | null) => {
    if (!incoming) return;
    const next = [...files];
    for (const file of Array.from(incoming)) {
      const ext = fileExtension(file.name);
      if (!ALLOWED_EXTENSIONS.has(ext)) {
        setError(t('feedback.invalidType'));
        continue;
      }
      if (file.size > MAX_FILE_BYTES) {
        setError(t('feedback.fileTooLarge'));
        continue;
      }
      if (next.length >= MAX_FILES) {
        setError(t('feedback.tooManyFiles'));
        break;
      }
      next.push(file);
    }
    setFiles(next);
    if (inputRef.current) {
      inputRef.current.value = '';
    }
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
      await FeedbackApi.submit({ kind, description: trimmed, files });
      toast.success(t('feedback.success'));
      close();
      reset();
    } catch {
      setError(t('feedback.submitError'));
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!mounted || !isOpen) {
    return null;
  }

  const trimmedLength = description.trim().length;
  const canSubmit = trimmedLength >= MIN_DESCRIPTION && !isSubmitting;

  return createPortal(
    <Theme
      accentColor="jade"
      grayColor="olive"
      appearance={appearance}
      radius="medium"
      data-accent-color="emerald"
    >
      <style>{`
        @keyframes ph-feedback-in {
          from { opacity: 0; transform: translateY(14px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}</style>
      <Box
        role="dialog"
        aria-modal="false"
        aria-labelledby="send-feedback-title"
        style={{
          position: 'fixed',
          bottom: 'max(20px, env(safe-area-inset-bottom, 0px))',
          right: 'max(20px, env(safe-area-inset-right, 0px))',
          zIndex: 9200,
          width: 400,
          maxWidth: 'calc(100vw - 32px)',
          maxHeight: 'min(680px, calc(100vh - 40px))',
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: 'var(--color-panel-solid)',
          borderRadius: 16,
          border: '1px solid var(--olive-a4)',
          boxShadow: '0 16px 48px rgba(0, 0, 0, 0.28)',
          overflow: 'hidden',
          animation: 'ph-feedback-in 180ms ease-out',
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
              <Text id="send-feedback-title" size="3" weight="medium">
                {t('feedback.title')}
              </Text>
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

        <Box
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            padding: '4px 16px 16px',
          }}
        >
          <Flex direction="column" gap="3">
            <Flex gap="2">
              <KindChip
                selected={kind === 'issue'}
                icon="bug_report"
                label={t('feedback.kindIssue')}
                disabled={isSubmitting}
                onSelect={() => setKind('issue')}
              />
              <KindChip
                selected={kind === 'feedback'}
                icon="chat_bubble"
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
              accept={ACCEPT}
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
                cursor: isSubmitting || files.length >= MAX_FILES ? 'default' : 'pointer',
                transition: 'background-color 140ms ease, border-color 140ms ease',
              }}
            >
              <button
                type="button"
                disabled={isSubmitting || files.length >= MAX_FILES}
                aria-label={t('feedback.addFiles')}
                onDragOver={(event) => {
                  event.preventDefault();
                  if (!isSubmitting && files.length < MAX_FILES) {
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
                  if (!isSubmitting && files.length < MAX_FILES) {
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

        <Box style={{ flexShrink: 0, padding: '0 16px 16px' }}>
          <Button
            size="3"
            style={{ width: '100%' }}
            onClick={handleSubmit}
            disabled={!canSubmit}
          >
            {isSubmitting ? <Spinner size="2" /> : t('feedback.submit')}
          </Button>
        </Box>
      </Box>
    </Theme>,
    document.body,
  );
}
