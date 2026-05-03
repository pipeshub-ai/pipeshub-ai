'use client';

import { Box, Flex, Text, IconButton, Dialog, VisuallyHidden } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FolderIcon } from '@/app/components/ui';
import type { KnowledgeHubApiResponse } from '../../types';

interface FolderDetailsSidebarProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tableData: KnowledgeHubApiResponse | null;
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
        background: 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-4)',
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--space-2)',
      }}
    >
      <Text size="1" weight="medium" style={{ color: 'var(--slate-10)' }}>
        {label}
      </Text>
      <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
        {displayValue}
      </Text>
    </Box>
  );
}

function formatNodeType(nodeType: string): string {
  switch (nodeType) {
    case 'kb': return 'Collection';
    case 'folder': return 'Folder';
    case 'recordGroup': return 'Record Group';
    case 'app': return 'Connector';
    default: return nodeType;
  }
}

function formatTimestamp(timestamp: number | undefined): string | undefined {
  if (!timestamp) return undefined;
  const date = new Date(timestamp);
  const pad = (n: number) => n.toString().padStart(2, '0');
  return `${pad(date.getMonth() + 1)}/${pad(date.getDate())}/${date.getFullYear()}, ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function FolderDetailsSidebar({ open, onOpenChange, tableData }: FolderDetailsSidebarProps) {
  const currentNode = tableData?.currentNode;
  const breadcrumbs = tableData?.breadcrumbs;
  const permissions = tableData?.permissions;
  const counts = tableData?.counts;

  const originName = breadcrumbs && breadcrumbs.length > 0 ? breadcrumbs[0].name : undefined;
  const totalItems = counts?.total != null ? counts.total.toString() : undefined;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Content
        style={{
          position: 'fixed',
          top: 10,
          right: 10,
          bottom: 10,
          width: '37.5rem',
          maxWidth: '100vw',
          maxHeight: 'calc(100vh - 20px)',
          padding: 0,
          margin: 0,
          backgroundColor: 'var(--effects-translucent)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-2)',
          backdropFilter: 'blur(25px)',
          boxShadow: '0 20px 48px 0 rgba(0, 0, 0, 0.25)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          transform: 'none',
          animation: 'slideInFromRight 0.2s ease-out',
        }}
      >
        <VisuallyHidden>
          <Dialog.Title>{currentNode?.name || 'Folder Details'}</Dialog.Title>
        </VisuallyHidden>

        {/* Header */}
        <Flex
          align="center"
          justify="between"
          style={{
            padding: '8px 8px 8px 16px',
            borderBottom: '1px solid var(--olive-3)',
            backgroundColor: 'var(--effects-translucent)',
            backdropFilter: 'blur(8px)',
          }}
        >
          <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
            <FolderIcon variant="default" size={24} color="var(--emerald-11)" />
            <Text
              size="2"
              weight="medium"
              style={{
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                color: 'var(--slate-12)',
              }}
            >
              {currentNode?.name || 'Folder'}
            </Text>
          </Flex>

          <Flex align="center" gap="1">
            <IconButton
              variant="ghost"
              color="gray"
              size="2"
              onClick={() => onOpenChange(false)}
            >
              <MaterialIcon name="close" size={16} color="var(--slate-11)" />
            </IconButton>
          </Flex>
        </Flex>

        {/* Content */}
        <Box
          className="no-scrollbar"
          style={{
            flex: 1,
            overflow: 'auto',
            padding: 'var(--space-4)',
          }}
        >
          {!currentNode ? (
            <Flex
              align="center"
              justify="center"
              style={{
                height: '100%',
                padding: 'var(--space-6)',
              }}
            >
              <Text size="2" style={{ color: 'var(--olive-a11)' }}>
                No folder details available
              </Text>
            </Flex>
          ) : (
            <Flex direction="column" gap="5">
              {/* Metadata Section */}
              <Flex direction="column" gap="2">
                <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  Metadata
                </Text>

                <Flex direction="column" gap="2">
                  <DetailRow label="Name" value={currentNode.name} />
                  <DetailRow label="Record Type" value={formatNodeType(currentNode.nodeType)} />
                  <DetailRow label="Origin" value={originName} />
                  <DetailRow label="Indexing Status" value={currentNode.indexingStatus} />
                  <DetailRow label="Total items" value={totalItems} />
                  <DetailRow label="Version" value={currentNode.version?.toString()} />
                  <DetailRow label="Created At" value={formatTimestamp(currentNode.createdAt)} />
                  <DetailRow label="Updated At" value={formatTimestamp(currentNode.updatedAt)} />
                  <DetailRow label="Permissions" value={permissions?.role} />
                </Flex>
              </Flex>
            </Flex>
          )}
        </Box>
      </Dialog.Content>
    </Dialog.Root>
  );
}
