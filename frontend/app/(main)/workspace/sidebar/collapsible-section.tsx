'use client';

import { useState } from 'react';
import { Flex } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ELEMENT_HEIGHT, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import { SIDEBAR_FOCUS_CLASS } from '@/app/components/sidebar/sidebar-focus-style';

interface CollapsibleSectionProps {
  /** Icon name for the section header (Google Material Icon) */
  icon: string;
  /** Label for the collapsible header */
  label: string;
  /** Whether the section is expanded */
  isExpanded: boolean;
  /** Toggle expand/collapse */
  onToggle: () => void;
  /** Child items to render when expanded */
  children: React.ReactNode;
}

/**
 * Collapsible sidebar section — used for the "People" dropdown.
 *
 * Header row: icon + label + expand_more chevron. It is a disclosure, not a page, so it only ever shows the hover
 * fill (the subtle fill, lighter than selected); the selected look belongs to the row of the page you are on.
 * Children rendered below when expanded.
 */
export function CollapsibleSection({
  icon,
  label,
  isExpanded,
  onToggle,
  children,
}: CollapsibleSectionProps) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <Flex direction="column" style={{ width: '100%' }}>
      {/* Collapsible header */}
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isExpanded}
        className={SIDEBAR_FOCUS_CLASS}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        style={{
          // Reset native button defaults
          appearance: 'none',
          margin: 0,
          font: 'inherit',
          color: 'inherit',
          textDecoration: 'none',
          // Layout
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-2)',
          width: '100%',
          height: ELEMENT_HEIGHT,
          padding: '0 12px',
          boxSizing: 'border-box',
          flexShrink: 0,
          // Visual
          borderRadius: 'var(--radius-1)',
          backgroundColor: isHovered ? 'var(--surface-subtle)' : 'transparent',
          border: '1px solid transparent',
          transition: 'background-color var(--motion-micro) var(--motion-ease)',
          cursor: 'pointer',
        }}
      >
        <MaterialIcon name={icon} size={ICON_SIZE_DEFAULT} color="var(--slate-11)" />
        <span
          style={{
            flex: 1,
            fontSize: 14,
            fontWeight: 400,
            lineHeight: '20px',
            color: 'var(--text-secondary)',
            textAlign: 'left',
          }}
        >
          {label}
        </span>
        <MaterialIcon
          name="expand_more"
          size={ICON_SIZE_DEFAULT}
          color="var(--slate-11)"
          style={{
            transition: 'transform 150ms ease',
            transform: isExpanded ? 'rotate(0deg)' : 'rotate(-90deg)',
          }}
        />
      </button>

      {/* Children (sub-items) */}
      {isExpanded && children}
    </Flex>
  );
}
