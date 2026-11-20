'use client';

import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, IconButton, Text, TextArea } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { RecentError } from '@/lib/support/recent-errors';
import {
  ALLOWED_ATTACHMENT_EXTENSIONS,
  ATTACHMENT_ACCEPT,
  fileExtension,
  MAX_ATTACHMENT_BYTES,
  MAX_ATTACHMENTS,
  MAX_ATTACHMENTS_TOTAL_BYTES,
} from '../../feedback/attachment-rules';
import { SUPPORT_AREA_META } from '../areas';
import type { SupportAreaKey, SupportDraft, SupportRow } from '../types';
import { DetailsTable } from './details-table';
import { SmtpNotice } from './smtp-notice';

export const MIN_DESCRIPTION = 10;
export const MAX_DESCRIPTION = 5000;

export function DetailsStep({
  area,
  draft,
  onChange,
  onChangeArea,
  isAdmin,
  smtpConfigured,
  supportEmail,
  disabled,
  detailRows,
  sentSummary,
  recentErrors,
  describeError,
}: {
  area: SupportAreaKey;
  draft: SupportDraft;
  onChange: (patch: Partial<SupportDraft>) => void;
  onChangeArea: () => void;
  isAdmin: boolean;
  smtpConfigured: boolean;
  supportEmail: string;
  disabled: boolean;
  detailRows: SupportRow[];
  sentSummary: string;
  recentErrors: RecentError[];
  describeError: (error: RecentError) => string;
}) {
  const { t } = useTranslation();
  const fileInput = useRef<HTMLInputElement>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [descriptionFocused, setDescriptionFocused] = useState(false);
  const descriptionHint =
    draft.description.length >= MAX_DESCRIPTION
      ? t('support.details.descriptionMax', { count: MAX_DESCRIPTION })
      : draft.description.trim().length < MIN_DESCRIPTION
        ? t('support.details.descriptionMin', { count: MIN_DESCRIPTION })
        : null;

  const addFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    const next = [...draft.files];
    const problems: string[] = [];
    for (const file of Array.from(incoming)) {
      if (next.length >= MAX_ATTACHMENTS) {
        problems.push(t('support.details.tooManyFiles'));
        break;
      }
      if (!ALLOWED_ATTACHMENT_EXTENSIONS.has(fileExtension(file.name))) {
        problems.push(t('support.details.fileUnsupported', { name: file.name }));
        continue;
      }
      if (file.size > MAX_ATTACHMENT_BYTES) {
        problems.push(t('support.details.fileTooLarge', { name: file.name }));
        continue;
      }
      if (next.reduce((sum, kept) => sum + kept.size, file.size) > MAX_ATTACHMENTS_TOTAL_BYTES) {
        problems.push(t('support.details.filesTotalTooLarge'));
        continue;
      }
      next.push(file);
    }
    setFileError(problems.length > 0 ? problems.join(' ') : null);
    onChange({ files: next });
    if (fileInput.current) fileInput.current.value = '';
  };

  return (
    <Flex direction="column" px="5" style={{ gap: 20 }}>
      {!smtpConfigured && <SmtpNotice supportEmail={supportEmail} isAdmin={isAdmin} />}

      <Flex
        align="center"
        gap="3"
        style={{
          padding: 'var(--space-3)',
          borderRadius: 'var(--radius-4)',
          border: '1px solid var(--olive-a4)',
          background: 'var(--gray-a2)',
        }}
      >
        <Flex
          aria-hidden="true"
          align="center"
          justify="center"
          flexShrink="0"
          style={{
            width: 36,
            height: 36,
            borderRadius: 'var(--radius-3)',
            background: 'var(--accent-a3)',
            color: 'var(--accent-11)',
          }}
        >
          <MaterialIcon name={SUPPORT_AREA_META[area].icon} size={20} />
        </Flex>
        <Box style={{ flex: 1, minWidth: 0 }}>
          <Text as="div" size="2" weight="bold" style={{ color: 'var(--slate-12)', lineHeight: '18px' }}>
            {t(`support.areas.${area}.label`)}
          </Text>
          <Text as="div" size="1" style={{ color: 'var(--slate-11)', lineHeight: '16px', marginTop: 2 }}>
            {t(`support.areas.${area}.hint`)}
          </Text>
        </Box>
        <Button
          type="button"
          variant="ghost"
          size="2"
          aria-label={t('support.changeArea')}
          onClick={onChangeArea}
          disabled={disabled}
          style={{ margin: 0, fontWeight: 600 }}
        >
          {t('support.change')}
        </Button>
      </Flex>

      <Flex direction="column" gap="2">
        <Text as="label" htmlFor="support-description" size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('support.details.descriptionLabel')}
          <Text aria-hidden="true" style={{ color: 'var(--red-11)', marginLeft: 2 }}>
            *
          </Text>
        </Text>
        <Box
          style={{
            borderRadius: 'var(--radius-4)',
            border: '1px solid var(--gray-a7)',
            background: 'var(--color-surface)',
            overflow: 'hidden',
            outline: descriptionFocused ? '2px solid var(--focus-8)' : 'none',
            outlineOffset: -1,
          }}
        >
          <TextArea
            id="support-description"
            value={draft.description}
            onChange={(event) => onChange({ description: event.target.value.slice(0, MAX_DESCRIPTION) })}
            onFocus={() => setDescriptionFocused(true)}
            onBlur={() => setDescriptionFocused(false)}
            placeholder={t('support.details.descriptionPlaceholder')}
            aria-required
            aria-describedby={descriptionHint ? 'support-description-hint' : undefined}
            rows={4}
            disabled={disabled}
            // The box around the text, files and toolbar draws the border and focus ring.
            style={{
              padding: 'var(--space-1) var(--space-2)',
              boxShadow: 'none',
              outline: 'none',
              backgroundColor: 'transparent',
            }}
          />
          {draft.files.length > 0 && (
            <Flex wrap="wrap" gap="2" px="3" pb="3">
              {draft.files.map((file, index) => (
                <Flex
                  key={`${file.name}-${index}`}
                  align="center"
                  gap="1"
                  style={{
                    height: 28,
                    padding: '0 4px 0 8px',
                    borderRadius: 'var(--radius-2)',
                    background: 'var(--gray-a4)',
                    maxWidth: '100%',
                  }}
                >
                  <MaterialIcon name="image" size={14} color="var(--slate-11)" />
                  <Text size="1" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 220 }}>
                    {file.name}
                  </Text>
                  <IconButton
                    className="touch-hit"
                    type="button"
                    size="1"
                    variant="ghost"
                    color="gray"
                    disabled={disabled}
                    aria-label={t('support.details.removeFile', { name: file.name })}
                    onClick={() => {
                      setFileError(null);
                      onChange({ files: draft.files.filter((_, i) => i !== index) });
                    }}
                    style={{ margin: 0 }}
                  >
                    <MaterialIcon name="close" size={12} />
                  </IconButton>
                </Flex>
              ))}
            </Flex>
          )}
          <Flex
            align="center"
            justify="between"
            gap="3"
            style={{
              padding: 'var(--space-2) var(--space-2) var(--space-2) var(--space-3)',
              borderTop: '1px solid var(--gray-a4)',
              background: 'var(--gray-a2)',
            }}
          >
            {descriptionHint ? (
              <Text id="support-description-hint" size="1" style={{ color: 'var(--slate-11)' }}>
                {descriptionHint}
              </Text>
            ) : (
              <Text size="1" style={{ color: 'var(--slate-11)', fontVariantNumeric: 'tabular-nums' }}>
                {draft.description.length} / {MAX_DESCRIPTION}
              </Text>
            )}
            <input
              ref={fileInput}
              type="file"
              multiple
              hidden
              accept={ATTACHMENT_ACCEPT}
              onChange={(event) => addFiles(event.target.files)}
            />
            <Button
              type="button"
              variant="soft"
              color="gray"
              size="1"
              disabled={disabled || draft.files.length >= MAX_ATTACHMENTS}
              onClick={() => fileInput.current?.click()}
              style={{ flexShrink: 0 }}
            >
              <MaterialIcon name="attach_file" size={14} />
              {t('support.details.attach')}
            </Button>
          </Flex>
        </Box>
        {fileError && (
          <Text size="1" role="alert" style={{ color: 'var(--red-11)' }}>
            {fileError}
          </Text>
        )}
      </Flex>

      <DetailsTable
        summary={sentSummary}
        rows={detailRows}
        recentErrors={recentErrors}
        includeErrors={draft.includeErrors}
        onIncludeErrorsChange={(includeErrors) => onChange({ includeErrors })}
        describeError={describeError}
      />
    </Flex>
  );
}
