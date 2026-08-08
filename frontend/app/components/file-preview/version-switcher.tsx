'use client';

import { useEffect, useRef, useState } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';

interface VersionSwitcherProps {
  /** Version currently loaded in the preview. */
  version: number;
  /** Highest version that exists for this artifact. */
  latestVersion: number;
  onVersionChange: (version: number) => void | Promise<void>;
  /** True while a switch triggered by `onVersionChange` is in flight. */
  isSwitching?: boolean;
}

/**
 * Claude-style version pill: shows "v{n}" and, when older versions exist,
 * expands into a dropdown listing every version 1..latestVersion (newest
 * first) so the user can jump directly to any prior render of an artifact
 * instead of only ever seeing the newest copy.
 */
export function VersionSwitcher({
  version,
  latestVersion,
  onVersionChange,
  isSwitching = false,
}: VersionSwitcherProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const canSwitch = latestVersion > 1;

  useEffect(() => {
    if (!open) return undefined;
    const handleOutsideClick = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handleOutsideClick);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleOutsideClick);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [open]);

  // Close the dropdown once a switch we requested resolves.
  useEffect(() => {
    if (isSwitching) setOpen(false);
  }, [isSwitching]);

  const handleSelect = (targetVersion: number) => {
    setOpen(false);
    if (targetVersion === version) return;
    void onVersionChange(targetVersion);
  };

  const versions = Array.from({ length: latestVersion }, (_, i) => latestVersion - i);

  return (
    <Box ref={containerRef} style={{ position: 'relative', flexShrink: 0 }}>
      <Flex
        align="center"
        gap="1"
        onClick={() => canSwitch && !isSwitching && setOpen((v) => !v)}
        role={canSwitch ? 'button' : undefined}
        aria-haspopup={canSwitch ? 'listbox' : undefined}
        aria-expanded={canSwitch ? open : undefined}
        aria-label={canSwitch ? `Version ${version}, switch version` : `Version ${version}`}
        title={canSwitch ? 'Switch version' : undefined}
        style={{
          cursor: canSwitch && !isSwitching ? 'pointer' : 'default',
          color: 'var(--accent-11)',
          backgroundColor: 'var(--accent-3)',
          borderRadius: 'var(--radius-1)',
          padding: '2px 6px',
          userSelect: 'none',
        }}
      >
        {isSwitching ? (
          <Spinner size={12} />
        ) : (
          <Text size="1" weight="medium">
            v{version}
          </Text>
        )}
        {canSwitch && !isSwitching && (
          <MaterialIcon name="expand_more" size={14} color="var(--accent-11)" />
        )}
      </Flex>

      {open && canSwitch && (
        <Box
          role="listbox"
          style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            marginTop: 4,
            backgroundColor: 'var(--color-panel-solid)',
            border: '1px solid var(--slate-6)',
            borderRadius: 'var(--radius-2)',
            boxShadow: '0px 8px 24px rgba(0, 0, 0, 0.15)',
            zIndex: 30,
            minWidth: 140,
            overflow: 'hidden',
          }}
        >
          {versions.map((v) => {
            const isCurrent = v === version;
            return (
              <Flex
                key={v}
                role="option"
                aria-selected={isCurrent}
                align="center"
                justify="between"
                gap="3"
                onClick={() => handleSelect(v)}
                style={{
                  padding: '6px 10px',
                  cursor: isCurrent ? 'default' : 'pointer',
                  backgroundColor: isCurrent ? 'var(--accent-3)' : 'transparent',
                }}
                onMouseEnter={(e) => {
                  if (!isCurrent) e.currentTarget.style.backgroundColor = 'var(--slate-3)';
                }}
                onMouseLeave={(e) => {
                  if (!isCurrent) e.currentTarget.style.backgroundColor = 'transparent';
                }}
              >
                <Text size="2" style={{ color: 'var(--slate-12)', whiteSpace: 'nowrap' }}>
                  Version {v}
                  {v === latestVersion ? ' (latest)' : ''}
                </Text>
                {isCurrent && <MaterialIcon name="check" size={16} color="var(--accent-11)" />}
              </Flex>
            );
          })}
        </Box>
      )}
    </Box>
  );
}
