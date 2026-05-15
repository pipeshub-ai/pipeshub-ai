'use client';

import { useCallback, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { Box, Flex, Tabs, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { formatDate } from '@/lib/utils/formatters';
import { formatFileSize } from '@/app/components/file-preview/utils';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import type { RecordDetailsResponse } from '@/app/(main)/knowledge-base/types';

interface RecordMetadataPanelProps {
  recordDetails: RecordDetailsResponse;
}

const SECTION_GROUP_STYLE: CSSProperties = {
  border: '1px solid var(--olive-3)',
  borderRadius: 'var(--radius-2)',
  overflow: 'hidden',
  width: '100%',
};

const ROW_STYLE: CSSProperties = {
  display: 'flex',
  alignItems: 'baseline',
  gap: 'var(--space-3)',
  padding: 'var(--space-2) var(--space-3)',
  borderBottom: '1px solid var(--olive-3)',
  minWidth: 0,
};

const ROW_LAST_STYLE: CSSProperties = {
  display: 'flex',
  alignItems: 'baseline',
  gap: 'var(--space-3)',
  padding: 'var(--space-2) var(--space-3)',
  minWidth: 0,
};

const LABEL_STYLE: CSSProperties = {
  color: 'var(--olive-10)',
  minWidth: '38%',
  flexShrink: 0,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
};

/** Scales with viewport so narrow panels truncate; wide screens show more text before ellipsis. Tooltip when truncated: TruncatingText. */
const METADATA_CHIP_STYLE: CSSProperties = {
  backgroundColor: 'var(--olive-2)',
  border: '1px solid var(--olive-3)',
  borderRadius: 'var(--radius-2)',
  padding: 'var(--space-1) var(--space-3)',
  maxWidth: 'min(100%, clamp(10rem, 50vw, 42rem))',
  minWidth: 0,
  width: 'fit-content',
  overflow: 'hidden',
};

const CHIP_STYLE: CSSProperties = {
  backgroundColor: 'var(--olive-2)',
  border: '1px solid var(--olive-3)',
  borderRadius: 'var(--radius-2)',
  padding: 'var(--space-1) var(--space-3)',
  maxWidth: 'min(100%, 240px)',
  minWidth: 0,
  overflow: 'hidden',
};

function permissionLabel(
  relationship: string | undefined,
  t: (key: string) => string,
): string {
  switch (relationship) {
    case 'OWNER':
      return t('recordView.permissionOwner');
    case 'READER':
      return t('recordView.permissionReader');
    case 'WRITER':
      return t('recordView.permissionWriter');
    default:
      return relationship || t('recordView.permissionOwner');
  }
}

function formatTimestamp(ts: number | undefined | null): string | undefined {
  if (ts === undefined || ts === null || Number.isNaN(ts)) return undefined;
  return formatDate(ts);
}

function readString(obj: Record<string, unknown> | null | undefined, key: string): string | undefined {
  if (!obj) return undefined;
  const v = obj[key];
  return typeof v === 'string' ? v : undefined;
}

function readStringArray(
  obj: Record<string, unknown> | null | undefined,
  key: string,
): string[] | undefined {
  if (!obj) return undefined;
  const v = obj[key];
  if (!Array.isArray(v)) return undefined;
  const out = v.filter((x): x is string => typeof x === 'string');
  return out.length ? out : undefined;
}

function hasValidNames(items: Array<{ id: string; name: string }> | undefined): boolean {
  return Boolean(items?.some((item) => item?.name?.trim()));
}

function humanizeMetadataKey(key: string): string {
  const spaced = key.replace(/([a-z])(\d+)$/, '$1 $2');
  const words = spaced.replace(/([a-z])([A-Z])/g, '$1 $2');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function humanizeIndexingStatus(status: string | undefined): string {
  if (!status) return '—';
  return status.replace(/_/g, ' ');
}

type IndexingStatusColors = { bg: string; text: string; border: string };

function indexingStatusColors(status: string | undefined): IndexingStatusColors {
  switch (status) {
    case 'COMPLETED':
      return { bg: 'var(--green-3)', text: 'var(--green-11)', border: 'var(--green-6)' };
    case 'IN_PROGRESS':
    case 'QUEUED':
      return { bg: 'var(--blue-3)', text: 'var(--blue-11)', border: 'var(--blue-6)' };
    case 'FAILED':
      return { bg: 'var(--red-3)', text: 'var(--red-11)', border: 'var(--red-6)' };
    case 'FILE_TYPE_NOT_SUPPORTED':
      return { bg: 'var(--orange-3)', text: 'var(--orange-11)', border: 'var(--orange-6)' };
    case 'AUTO_INDEX_OFF':
    case 'NOT_STARTED':
      return { bg: 'var(--olive-3)', text: 'var(--olive-11)', border: 'var(--olive-5)' };
    default:
      return { bg: 'var(--olive-3)', text: 'var(--olive-11)', border: 'var(--olive-5)' };
  }
}

function IndexingStatusChip({ status }: { status: string | undefined }) {
  const label = humanizeIndexingStatus(status);
  const { bg, text, border } = indexingStatusColors(status);
  return (
    <Box
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        backgroundColor: bg,
        border: `1px solid ${border}`,
        borderRadius: '2px',
        padding: '2px var(--space-2)',
        flexShrink: 0,
      }}
    >
      <Text
        size="2"
        weight="medium"
        style={{ color: text, whiteSpace: 'nowrap', textTransform: 'uppercase', letterSpacing: '0.04em' }}
      >
        {label}
      </Text>
    </Box>
  );
}

function shouldShowIndexingReasonTooltip(status: string | undefined, reason: string | undefined): boolean {
  if (!reason?.trim()) return false;
  return (
    status === 'FAILED' ||
    status === 'AUTO_INDEX_OFF' ||
    status === 'FILE_TYPE_NOT_SUPPORTED' ||
    status === 'NOT_STARTED'
  );
}

function formatDisplayType(
  record: RecordDetailsResponse['record'],
  t: (key: string, options?: { defaultValue?: string }) => string,
): string {
  const { recordType, fileRecord, mimeType } = record;
  if (recordType === 'FILE' && fileRecord) {
    const ext = fileRecord.extension?.trim();
    if (ext) return ext.toUpperCase();
    const mime = fileRecord.mimeType?.trim() || mimeType?.trim();
    return mime || t('recordView.labels.notAvailable');
  }
  if (recordType === 'EMAIL') return t('recordView.labels.typeEmail');
  if (recordType === 'TICKET') return t('recordView.labels.typeTicket');
  if (recordType === 'WEBPAGE') return t('recordView.labels.typeWebpage');
  if (recordType === 'MESSAGE') return t('recordView.labels.typeMessage');
  return recordType;
}

function formatFileSizeDisplay(record: RecordDetailsResponse['record']): string | undefined {
  if (record.recordType !== 'FILE') return undefined;
  const raw = record.sizeInBytes ?? record.fileRecord?.sizeInBytes;
  if (raw === undefined || raw === null || Number.isNaN(raw) || raw < 0) return undefined;
  return formatFileSize(raw);
}

function primaryDocumentLabel(
  record: RecordDetailsResponse['record'],
  t: (key: string) => string,
): string {
  if (record.recordType === 'EMAIL') return t('recordView.labels.mailSubject');
  if (record.recordType === 'TICKET') return t('recordView.labels.ticketSummary');
  return t('recordView.labels.fileName');
}

function primaryDocumentValue(record: RecordDetailsResponse['record']): string | undefined {
  if (record.recordType === 'FILE' && record.fileRecord?.name?.trim()) {
    return record.fileRecord.name.trim();
  }
  if (record.recordType === 'EMAIL') {
    const subj = readString(record.mailRecord, 'subject')?.trim();
    return subj || record.recordName?.trim();
  }
  if (record.recordType === 'TICKET') {
    const name = readString(record.ticketRecord, 'name')?.trim();
    return name || record.recordName?.trim();
  }
  return record.recordName?.trim();
}

function originDisplay(record: RecordDetailsResponse['record'], t: (key: string) => string): string {
  if (record.origin === 'CONNECTOR') return t('recordView.labels.originConnector');
  return t('recordView.labels.originUpload');
}

function TruncatingText({
  text,
  size = '3',
  style,
}: {
  text: string;
  size?: '1' | '2' | '3';
  style?: CSSProperties;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const [isTruncated, setIsTruncated] = useState(false);

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    setIsTruncated(el.scrollWidth > el.clientWidth);
  }, []);

  useLayoutEffect(() => {
    measure();
    const el = ref.current;
    const ro = el ? new ResizeObserver(measure) : null;
    if (el && ro) ro.observe(el);
    window.addEventListener('resize', measure);
    return () => {
      ro?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [measure, text]);

  const inner = (
    <Text
      ref={ref}
      size={size}
      style={{
        color: 'var(--olive-12)',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        minWidth: 0,
        width: '100%',
        ...style,
      }}
    >
      {text}
    </Text>
  );

  if (!text || text === '—') {
    return inner;
  }

  return (
    <Tooltip content={text}>
      {inner}
    </Tooltip>
  );
}

function DetailRow({
  label,
  value,
  monospace,
  isLast,
}: {
  label: string;
  value: string | null | undefined;
  monospace?: boolean;
  isLast?: boolean;
}) {
  const display = value?.trim() ? value.trim() : '—';
  return (
    <Flex style={isLast ? ROW_LAST_STYLE : ROW_STYLE}>
      <Text size="2" weight="medium" style={LABEL_STYLE}>
        {label}
      </Text>
      <TruncatingText
        text={display}
        size="2"
        style={monospace ? { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace' } : undefined}
      />
    </Flex>
  );
}

function MetadataChipRow({ items }: { items: Array<{ id: string; name: string }> }) {
  const valid = items.filter((i) => i?.name?.trim());
  if (!valid.length) return null;
  return (
    <Flex wrap="wrap" gap="2">
      {valid.map((item) => (
        <Box key={item.id} style={METADATA_CHIP_STYLE}>
          <TruncatingText text={item.name.trim()} size="2" />
        </Box>
      ))}
    </Flex>
  );
}

function MetadataSection({
  title,
  items,
}: {
  title: string;
  items: Array<{ id: string; name: string }> | undefined;
}) {
  if (!hasValidNames(items) || !items) return null;
  return (
    <Box style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', minWidth: 0 }}>
      <Text
        size="2"
        weight="medium"
        style={{
          color: 'var(--olive-10)',
          textTransform: 'uppercase',
          letterSpacing: '0.04em',
        }}
      >
        {title}
      </Text>
      <MetadataChipRow items={items} />
    </Box>
  );
}

/** Scroll + padding on tab body. Tabs.Content is the scroll host; inner Flex owns column layout. */
const RECORD_METADATA_TAB_CONTENT_STYLE: CSSProperties = {
  flex: 1,
  minHeight: 0,
  marginTop: 0,
  overflowY: 'auto',
  overscrollBehavior: 'contain',
  scrollbarGutter: 'stable',
  padding: 'var(--space-4)',
};

export function RecordMetadataPanel({ recordDetails }: RecordMetadataPanelProps) {
  const { t } = useTranslation();
  const { record, knowledgeBase, folder, permissions, metadata } = recordDetails;

  const createdDate = formatTimestamp(
    record.sourceCreatedAtTimestamp ?? record.createdAtTimestamp,
  );
  const updatedDate = formatTimestamp(
    record.sourceLastModifiedTimestamp ?? record.updatedAtTimestamp,
  );

  const showReasonHint = shouldShowIndexingReasonTooltip(record.indexingStatus, record.reason);
  const isAttachment = record.connectorName?.toUpperCase() === 'ATTACHMENTS';
  const connectorIconType = resolveConnectorType(record.connectorId || 'generic');
  const shortConnectorId =
    record.connectorId && record.connectorId.length > 12
      ? `${record.connectorId.slice(0, 8)}…`
      : record.connectorId;

  const fileSizeDisplay = formatFileSizeDisplay(record);
  const typeDisplay = formatDisplayType(record, t);
  const primaryLabel = primaryDocumentLabel(record, t);
  const primaryValue = primaryDocumentValue(record);
  const originText = originDisplay(record, t);

  const mailFrom = readString(record.mailRecord, 'from');
  const mailToRaw = record.mailRecord?.to;
  const mailTo =
    typeof mailToRaw === 'string'
      ? mailToRaw
      : Array.isArray(mailToRaw)
        ? mailToRaw.filter((x): x is string => typeof x === 'string').join(', ')
        : undefined;
  const mailCc = readStringArray(record.mailRecord, 'cc')?.join(', ');
  const mailLabels = readStringArray(record.mailRecord, 'labelIds');
  const mailDate = readString(record.mailRecord, 'date');

  const hasRecordInformation =
    Object.values(metadata ?? {}).some(hasValidNames) ||
    (record.recordType === 'EMAIL' &&
      ((mailLabels?.length ?? 0) > 0 || Boolean(mailDate || mailFrom || mailTo || mailCc))) ||
    (record.recordType === 'TICKET' &&
      Boolean(readString(record.ticketRecord, 'description')?.trim()));

  return (
    <Flex
      direction="column"
      style={{
        height: '100%',
        width: '100%',
        minHeight: 0,
        backgroundColor: 'var(--color-panel)',
      }}
    >
      <Tabs.Root
        defaultValue="document-details"
        style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, width: '100%' }}
      >
        <Box
          style={{
            position: 'sticky',
            top: 0,
            zIndex: 2,
            backgroundColor: 'var(--color-panel)',
            flexShrink: 0,
          }}
        >
          <Tabs.List
            style={{
              width: '100%',
              minWidth: 0,
              borderBottom: '1px solid var(--olive-3)',
              marginBottom: 'var(--space-3)',
              overflowX: 'auto',
              overscrollBehaviorX: 'contain',
            }}
          >
            <Tabs.Trigger value="document-details" style={{ flexShrink: 0 }}>
              {t('recordView.tabDocumentDetails')}
            </Tabs.Trigger>
            <Tabs.Trigger value="record-information" style={{ flexShrink: 0 }}>
              {t('recordView.tabRecordInformation')}
            </Tabs.Trigger>
          </Tabs.List>
        </Box>

        <Tabs.Content value="document-details" style={RECORD_METADATA_TAB_CONTENT_STYLE}>
          <Flex direction="column" gap="4" style={{ minWidth: 0 }}>

            {/* Document Summary */}
            <Text size="2" weight="medium" style={{ color: 'var(--olive-11)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              {t('recordView.sections.documentFields')}
            </Text>
            <Box style={SECTION_GROUP_STYLE}>
              <DetailRow label={primaryLabel} value={primaryValue} />
              <DetailRow label={t('recordView.labels.recordType')} value={typeDisplay} />
              <DetailRow label={t('recordView.labels.recordId')} value={record.id} monospace />
              <Flex style={{ ...ROW_STYLE, alignItems: 'center' }}>
                <Text size="2" weight="medium" style={LABEL_STYLE}>
                  {t('recordView.labels.indexingStatus')}
                </Text>
                <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
                  <IndexingStatusChip status={record.indexingStatus} />
                  {showReasonHint ? (
                    <Tooltip content={record.reason} delayDuration={200}>
                      <button
                        type="button"
                        aria-label={t('recordView.indexingReasonAria')}
                        style={{
                          border: 'none',
                          background: 'transparent',
                          padding: 0,
                          cursor: 'pointer',
                          display: 'inline-flex',
                          alignItems: 'center',
                          flexShrink: 0,
                        }}
                      >
                        <MaterialIcon name="info" size={16} color="var(--olive-11)" />
                      </button>
                    </Tooltip>
                  ) : null}
                </Flex>
              </Flex>
              {fileSizeDisplay ? (
                <DetailRow label={t('recordView.labels.fileSize')} value={fileSizeDisplay} />
              ) : null}
              {folder?.name ? (
                <DetailRow label={t('recordView.labels.folder')} value={folder.name} />
              ) : null}

              {/* Indexing Status row — chip with color coding + optional info icon */}

              <DetailRow
                label={t('recordView.labels.collection')}
                value={knowledgeBase?.name ?? undefined}
                isLast
              />
            </Box>

            {/* Document Fields */}



            {/* Permissions */}
            <Box style={SECTION_GROUP_STYLE}>
              <Flex style={ROW_LAST_STYLE} align="start">
                <Text size="2" weight="medium" style={{ ...LABEL_STYLE, paddingTop: 'var(--space-1)' }}>
                  {t('recordView.labels.permissions')}
                </Text>
                {permissions?.length ? (
                  <Flex wrap="wrap" gap="2" style={{ flex: 1, minWidth: 0 }}>
                    {permissions.map((p) => (
                      <Box key={p.id} style={CHIP_STYLE}>
                        <TruncatingText text={permissionLabel(p.relationship, t)} size="2" />
                      </Box>
                    ))}
                  </Flex>
                ) : (
                  <Text size="2" color="gray">
                    {t('recordView.labels.noPermissions')}
                  </Text>
                )}
              </Flex>
            </Box>

            {/* Connector */}
            <Box style={SECTION_GROUP_STYLE}>
              <Flex style={ROW_LAST_STYLE} align="center">
                <Text size="2" weight="medium" style={LABEL_STYLE}>
                  {t('recordView.labels.connector')}
                </Text>
                {record.origin === 'CONNECTOR' ? (
                  <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
                    <ConnectorIcon type={connectorIconType} size={20} />
                    <Flex direction="column" gap="1" style={{ minWidth: 0, flex: 1 }}>
                      <TruncatingText
                        text={record.connectorName?.trim() || t('recordView.externalSource')}
                        size="2"
                      />
                      {shortConnectorId ? (
                        <Tooltip content={record.connectorId} delayDuration={200}>
                          <Text size="2" style={{ color: 'var(--olive-10)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {t('recordView.labels.connectorId')}: {shortConnectorId}
                          </Text>
                        </Tooltip>
                      ) : null}
                    </Flex>
                  </Flex>
                ) : isAttachment ? (
                  <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
                    <MaterialIcon name="attach_file" size={20} color="var(--accent-9)" />
                    <Text size="2" style={{ color: 'var(--olive-12)' }}>
                      Chat Attachment
                    </Text>
                  </Flex>
                ) : (
                  <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
                    <MaterialIcon name="folder" size={20} color="var(--accent-9)" />
                    <Text size="2" style={{ color: 'var(--olive-12)' }}>
                      {t('recordView.collectionUpload')}
                    </Text>
                  </Flex>
                )}
              </Flex>
            </Box>
            <Box style={SECTION_GROUP_STYLE}>

              <DetailRow label={t('recordView.labels.origin')} value={originText} />
              <DetailRow label={t('recordView.labels.version')} value={record.version?.toString()} />
              <DetailRow label={t('recordView.labels.createdAt')} value={createdDate} />
              <DetailRow label={t('recordView.labels.updatedAt')} value={updatedDate} isLast />
            </Box>

          </Flex>
        </Tabs.Content>

        <Tabs.Content value="record-information" style={RECORD_METADATA_TAB_CONTENT_STYLE}>
          <Flex direction="column" gap="4" style={{ minWidth: 0 }}>
            {record.recordType === 'EMAIL' ? (
              <Flex direction="column" gap="4">
                {mailFrom ? <DetailRow label={t('recordView.mail.from')} value={mailFrom} /> : null}
                {mailTo ? <DetailRow label={t('recordView.mail.to')} value={mailTo} /> : null}
                {mailCc ? <DetailRow label={t('recordView.mail.cc')} value={mailCc} /> : null}
                {mailLabels?.length ? (
                  <Box style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', minWidth: 0 }}>
                    <Text size="2" weight="medium" style={{ color: 'var(--olive-10)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                      {t('recordView.mail.labels')}
                    </Text>
                    <MetadataChipRow
                      items={mailLabels.map((label, i) => ({
                        id: `${label}-${i}`,
                        name: label.split('_').join(' '),
                      }))}
                    />
                  </Box>
                ) : null}
                {mailDate ? <DetailRow label={t('recordView.mail.date')} value={mailDate} /> : null}
              </Flex>
            ) : null}

            {record.recordType === 'TICKET' && readString(record.ticketRecord, 'description')?.trim() ? (
              <DetailRow
                label={t('recordView.labels.ticketDescription')}
                value={readString(record.ticketRecord, 'description')}
              />
            ) : null}

            {(Object.entries(metadata ?? {}) as [string, Array<{ id: string; name: string }>][])
              .filter(([, items]) => hasValidNames(items))
              .map(([key, items]) => (
                <MetadataSection key={key} title={humanizeMetadataKey(key)} items={items} />
              ))}

            {!hasRecordInformation ? (
              <Text size="2" color="gray">
                {t('recordView.emptyState.noRecordMetadata')}
              </Text>
            ) : null}
          </Flex>
        </Tabs.Content>
      </Tabs.Root>
    </Flex>
  );
}
