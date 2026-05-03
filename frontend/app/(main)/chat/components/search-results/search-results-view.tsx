'use client';

import React from 'react';
import { Flex, Box, Text, Badge } from '@radix-ui/themes';
import { useChatStore } from '@/chat/store';
import { SearchResultCard } from './search-result-card';
import type { SearchResultItem } from '@/chat/types';

/**
 * Skeleton card for loading state.
 */
function SearchResultSkeleton() {
  return (
    <Flex
      direction="column"
      gap="3"
      style={{
        backgroundColor: 'var(--olive-2)',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        padding: 'var(--space-4)',
      }}
    >
      {/* Header skeleton */}
      <Flex align="center" justify="between">
        <Flex align="center" gap="2">
          <Box
            style={{
              width: 16,
              height: 16,
              borderRadius: 'var(--radius-1)',
              backgroundColor: 'var(--olive-4)',
              animation: 'pulse 1.5s ease-in-out infinite',
            }}
          />
          <Box
            style={{
              width: 200,
              height: 14,
              borderRadius: 'var(--radius-1)',
              backgroundColor: 'var(--olive-4)',
              animation: 'pulse 1.5s ease-in-out infinite',
            }}
          />
        </Flex>
        <Flex gap="2">
          <Box
            style={{
              width: 80,
              height: 24,
              borderRadius: 'var(--radius-1)',
              backgroundColor: 'var(--olive-4)',
              animation: 'pulse 1.5s ease-in-out infinite',
            }}
          />
          <Box
            style={{
              width: 48,
              height: 24,
              borderRadius: 'var(--radius-1)',
              backgroundColor: 'var(--olive-4)',
              animation: 'pulse 1.5s ease-in-out infinite',
            }}
          />
        </Flex>
      </Flex>
      {/* Content skeleton */}
      <Box
        style={{
          borderLeft: '4px solid var(--olive-4)',
          paddingLeft: 'var(--space-3)',
        }}
      >
        <Box
          style={{
            width: '100%',
            height: 40,
            borderRadius: 'var(--radius-1)',
            backgroundColor: 'var(--olive-4)',
            animation: 'pulse 1.5s ease-in-out infinite',
          }}
        />
      </Box>
      {/* Footer skeleton */}
      <Box
        style={{
          width: 100,
          height: 22,
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--olive-4)',
          animation: 'pulse 1.5s ease-in-out infinite',
        }}
      />
    </Flex>
  );
}

/**
 * Search results view — rendered when in search mode with results or a search in progress.
 */
export function SearchResultsView() {
  const searchResults = useChatStore((s) => s.searchResults);
  const searchQuery = useChatStore((s) => s.searchQuery);
  const isSearching = useChatStore((s) => s.isSearching);
  const searchError = useChatStore((s) => s.searchError);
  const setMode = useChatStore((s) => s.setMode);

  const handleOpenSource = (_result: SearchResultItem) => {
    // webUrl is already opened in SearchResultCard via window.open
  };

  const handleChat = (_result: SearchResultItem) => {
    // Switch to chat mode and clear search data
    setMode('chat');
    useChatStore.getState().clearSearchResults();
  };

  return (
    <Flex
      direction="column"
      style={{
        flex: 1,
        position: 'relative',
        zIndex: 10,
        maxWidth: '50rem',
        width: '100%',
        margin: '0 auto',
        overflow: 'hidden',
        paddingTop: 'var(--space-6)',
      }}
    >
      {/* Query heading */}
      {searchQuery && (
        <Box style={{ marginBottom: 'var(--space-4)', padding: '0 var(--space-2)' }}>
          <Text
            size="6"
            weight="medium"
            style={{
              color: 'var(--slate-12)',
              lineHeight: 'var(--line-height-6)',
            }}
          >
            {searchQuery}
          </Text>
        </Box>
      )}

      {/* "Results" tab header with count */}
      <Flex
        align="center"
        gap="2"
        style={{
          height: 'var(--space-7)',
          padding: '0 var(--space-2)',
          marginBottom: 'var(--space-2)',
        }}
      >
        <Box
          style={{
            height: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            paddingLeft: 'var(--space-2)',
            paddingRight: 'var(--space-2)',
            position: 'relative',
          }}
        >
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
            Results
          </Text>
          {/* Active tab underline */}
          <Box
            style={{
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              height: '2px',
              backgroundColor: 'var(--accent-10)',
            }}
          />
        </Box>
        {!isSearching && searchResults.length > 0 && (
          <Badge
            size="1"
            variant="soft"
            style={{
              background: 'var(--accent-a3)',
              color: 'var(--accent-a11)',
              fontWeight: 500,
              borderRadius: 'var(--radius-1)',
            }}
          >
            {searchResults.length}
          </Badge>
        )}
      </Flex>

      {/* Separator */}
      <Box
        style={{
          height: '1px',
          backgroundColor: 'var(--slate-a6)',
          marginLeft: 'var(--space-2)',
          marginRight: 'var(--space-2)',
          marginBottom: 'var(--space-4)',
        }}
      />

      {/* Results list */}
      <Flex
        direction="column"
        gap="2"
        className="no-scrollbar"
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '0 var(--space-2)',
          paddingBottom: 'var(--space-4)',
        }}
      >
        {/* Loading state */}
        {isSearching && (
          <>
            <SearchResultSkeleton />
            <SearchResultSkeleton />
            <SearchResultSkeleton />
          </>
        )}

        {/* Error state */}
        {!isSearching && searchError && (
          <Flex
            align="center"
            justify="center"
            style={{
              padding: 'var(--space-6)',
              color: 'var(--red-11)',
            }}
          >
            <Text size="2">{searchError}</Text>
          </Flex>
        )}

        {/* Empty state */}
        {!isSearching && !searchError && searchResults.length === 0 && searchQuery && (
          <Flex
            direction="column"
            align="center"
            justify="center"
            gap="2"
            style={{ padding: 'var(--space-6)' }}
          >
            <Text size="2" style={{ color: 'var(--slate-11)' }}>
              No results found for &ldquo;{searchQuery}&rdquo;
            </Text>
          </Flex>
        )}

        {/* Result cards */}
        {!isSearching && !searchError &&
          searchResults.map((result, index) => (
            <SearchResultCard
              key={`${result.metadata.recordId}-${result.block_index}-${index}`}
              result={result}
              onOpenSource={handleOpenSource}
              onChat={handleChat}
            />
          ))}
      </Flex>
    </Flex>
  );
}
