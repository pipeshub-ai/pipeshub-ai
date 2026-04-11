'use client';

import { Flex, Text, Box } from '@radix-ui/themes';
import { formatDate } from '@/lib/utils/formatters';
import type { RecordDetailsResponse } from '@/app/(main)/knowledge-base/types';

interface FileDetailsTabProps {
  recordDetails: RecordDetailsResponse | null;
}

interface DetailRowProps {
  label: string;
  value: string | string[] | null | undefined;
}

function DetailRow({ label, value }: DetailRowProps) {
  if (!value || (Array.isArray(value) && value.length === 0)) {
    return null;
  }

  const displayValue = Array.isArray(value) ? value.join(', ') : value;

  return (
    <Box
      style={{
        backgroundColor: 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-2)',
        padding: 'var(--space-4)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-1)',
      }}
    >
      <Text size="1" weight="medium" style={{ color: 'var(--olive-10)' }}>
        {label}
      </Text>
      <Text size="3" style={{ color: 'var(--olive-12)' }}>
        {displayValue}
      </Text>
    </Box>
  );
}

export function FileDetailsTab({ recordDetails }: FileDetailsTabProps) {
  if (!recordDetails) {
    return (
      <Flex
        align="center"
        justify="center"
        style={{
          height: '100%',
          padding: 'var(--space-6)',
        }}
      >
        <Text size="2" style={{ color: 'var(--olive-a11)' }}>
          No file details available
        </Text>
      </Flex>
    );
  }

  const { record, knowledgeBase, folder: _folder, metadata, permissions } = recordDetails;

  // Extract department names
  const _departments = metadata.departments.map(d => d.name).filter(Boolean);
  
  // Extract category names
  const _categories = metadata.categories.map(c => c.name).filter(Boolean);
  
  // Extract topic names
  const _topics = metadata.topics.map(t => t.name).filter(Boolean);
  
  // Format timestamps to date strings
  const createdDate = record.createdAtTimestamp 
    ? formatDate(new Date(record.createdAtTimestamp).toISOString()) 
    : undefined;
  const updatedDate = record.updatedAtTimestamp 
    ? formatDate(new Date(record.updatedAtTimestamp).toISOString()) 
    : undefined;

  return (
    <Flex
      direction="column"
      gap="5"
      style={{
        height: '100%',
        overflowY: 'auto',
      }}
      className="no-scrollbar"
    >
      {/* Metadata Section */}
      <Flex direction="column" gap="2">
        <Text size="3" weight="medium" style={{ color: 'var(--olive-12)' }}>
          Metadata
        </Text>
        
        <Flex direction="column" gap="2">
          <DetailRow label="Name" value={record.recordName} />
          <DetailRow label="Record Type" value={record.mimeType} />
          <DetailRow label="Origin" value={record.origin} />
          <DetailRow label="Indexing Status" value={record.indexingStatus} />
          <DetailRow label="Version" value={record.version?.toString()} />
          <DetailRow label="Created At" value={createdDate} />
          <DetailRow label="Updated At" value={updatedDate} />
          <DetailRow label="Knowledge Base" value={knowledgeBase?.name} />
          <DetailRow label="Permissions" value={permissions?.[0]?.relationship || 'Owner'} />
        </Flex>
      </Flex>

      {/* Record Information Section */}
      {/* <Flex direction="column" gap="2">
        <Text size="3" weight="medium" style={{ color: 'var(--olive-12)' }}>
          Record Information
        </Text>
        
        <Flex direction="column" gap="2">
          <DetailRow label="Name" value={record.recordName} />
          <DetailRow label="Record Type" value={record.recordType} />
          <DetailRow label="Origin" value={record.origin} />
          <DetailRow label="Indexing Status" value={record.indexingStatus} />
          <DetailRow label="Version" value={record.version?.toString()} />
          <DetailRow label="Created At" value={createdDate} />
          <DetailRow label="Updated At" value={updatedDate} />
          <DetailRow label="Knowledge Base" value={knowledgeBase?.name} />
          <DetailRow label="Permissions" value={permissions?.[0]?.relationship || 'Owner'} />
        </Flex>
      </Flex> */}
    </Flex>
  );
}
