'use client';

import React, { useState, useMemo } from 'react';
import { Flex, Text, Box, Button, Checkbox, TextField, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useConnectorsStore } from '../store';

// ========================================
// Component
// ========================================

export function SelectRecordsPage() {
  const {
    panelConnector,
    availableRecords,
    selectedRecords,
    isLoadingRecords,
    setSelectedRecords,
    setPanelView,
  } = useConnectorsStore();

  const [searchQuery, setSearchQuery] = useState('');

  const connectorName = panelConnector?.name ?? 'Connector';

  // Filter records by search
  const filteredRecords = useMemo(() => {
    if (!searchQuery.trim()) return availableRecords;
    const q = searchQuery.toLowerCase();
    return availableRecords.filter((r) =>
      r.name.toLowerCase().includes(q)
    );
  }, [availableRecords, searchQuery]);

  // Selection helpers
  const selectedSet = useMemo(() => new Set(selectedRecords), [selectedRecords]);
  const allFilteredSelected =
    filteredRecords.length > 0 &&
    filteredRecords.every((r) => selectedSet.has(r.id));

  const toggleRecord = (id: string) => {
    if (selectedSet.has(id)) {
      setSelectedRecords(selectedRecords.filter((r) => r !== id));
    } else {
      setSelectedRecords([...selectedRecords, id]);
    }
  };

  const toggleSelectAll = () => {
    if (allFilteredSelected) {
      // Deselect all filtered records
      const filteredIds = new Set(filteredRecords.map((r) => r.id));
      setSelectedRecords(selectedRecords.filter((r) => !filteredIds.has(r)));
    } else {
      // Select all filtered records (add to existing)
      const existingSet = new Set(selectedRecords);
      const newIds = filteredRecords
        .map((r) => r.id)
        .filter((id) => !existingSet.has(id));
      setSelectedRecords([...selectedRecords, ...newIds]);
    }
  };

  const handleBack = () => {
    setPanelView('tabs');
  };

  const handleAdd = () => {
    setPanelView('tabs');
  };

  return (
    <Flex
      direction="column"
      style={{
        height: '100%',
        overflow: 'hidden',
      }}
    >
      {/* ── Header ── */}
      <Flex
        align="center"
        gap="2"
        style={{
          padding: '0 0 12px 0',
          flexShrink: 0,
        }}
      >
        <IconButton
          variant="ghost"
          color="gray"
          size="2"
          onClick={handleBack}
          style={{ cursor: 'pointer' }}
        >
          <MaterialIcon name="arrow_back" size={18} color="var(--gray-11)" />
        </IconButton>
        <Flex align="center" gap="1">
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {connectorName}
          </Text>
          <MaterialIcon name="chevron_right" size={16} color="var(--gray-9)" />
          <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
            Select Records
          </Text>
        </Flex>
      </Flex>

      {/* ── Search ── */}
      <Box style={{ flexShrink: 0, paddingBottom: 12 }}>
        <TextField.Root
          size="2"
          placeholder="Search"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{ width: '100%' }}
        >
          <TextField.Slot>
            <MaterialIcon name="search" size={16} color="var(--gray-9)" />
          </TextField.Slot>
        </TextField.Root>
      </Box>

      {/* ── Records list ── */}
      <Box
        style={{
          flex: 1,
          overflow: 'auto',
          minHeight: 0,
        }}
      >
        {isLoadingRecords ? (
          <Flex align="center" justify="center" style={{ padding: 32 }}>
            <LottieLoader variant="loader" size={48} />
          </Flex>
        ) : filteredRecords.length === 0 ? (
          <Flex align="center" justify="center" style={{ padding: 32 }}>
            <Text size="2" style={{ color: 'var(--gray-10)' }}>
              {searchQuery ? 'No records match your search' : 'No records available'}
            </Text>
          </Flex>
        ) : (
          <Flex direction="column">
            {filteredRecords.map((record) => (
              <RecordRow
                key={record.id}
                name={record.name}
                checked={selectedSet.has(record.id)}
                onToggle={() => toggleRecord(record.id)}
              />
            ))}
          </Flex>
        )}
      </Box>

      {/* ── Footer ── */}
      <Flex
        align="center"
        justify="between"
        style={{
          flexShrink: 0,
          borderTop: '1px solid var(--gray-6)',
          padding: '12px 0',
          marginTop: 'auto',
        }}
      >
        <Flex align="center" gap="2">
          <Checkbox
            checked={allFilteredSelected && filteredRecords.length > 0}
            onCheckedChange={toggleSelectAll}
          />
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {selectedRecords.length} Selected
          </Text>
        </Flex>

        <Flex align="center" gap="2">
          <Button
            variant="outline"
            color="gray"
            size="2"
            onClick={handleBack}
            style={{ cursor: 'pointer' }}
          >
            <MaterialIcon name="arrow_back" size={14} color="var(--gray-11)" />
            Back
          </Button>
          <Button
            variant="solid"
            size="2"
            disabled={selectedRecords.length === 0}
            onClick={handleAdd}
            style={{
              cursor: selectedRecords.length === 0 ? 'not-allowed' : 'pointer',
            }}
          >
            Add Records
          </Button>
        </Flex>
      </Flex>
    </Flex>
  );
}

// ========================================
// Sub-components
// ========================================

function RecordRow({
  name,
  checked,
  onToggle,
}: {
  name: string;
  checked: boolean;
  onToggle: () => void;
}) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex
      align="center"
      gap="2"
      onClick={onToggle}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        height: 36,
        padding: '0 12px',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'transparent',
        cursor: 'pointer',
        transition: 'background-color 100ms ease',
        borderRadius: 'var(--radius-1)',
      }}
    >
      <Checkbox
        checked={checked}
        onCheckedChange={onToggle}
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
      />
      <Text size="2" style={{ color: 'var(--gray-12)' }}>
        {name}
      </Text>
    </Flex>
  );
}
