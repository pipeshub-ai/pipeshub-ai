'use client';

import React from 'react';
import { Flex, Text, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// Integration icon components
const SlackIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M3.36 10.08C3.36 10.9908 2.63077 11.72 1.72 11.72C0.809231 11.72 0.08 10.9908 0.08 10.08C0.08 9.16923 0.809231 8.44 1.72 8.44H3.36V10.08Z" fill="#E01E5A"/>
    <path d="M4.18 10.08C4.18 9.16923 4.90923 8.44 5.82 8.44C6.73077 8.44 7.46 9.16923 7.46 10.08V14.28C7.46 15.1908 6.73077 15.92 5.82 15.92C4.90923 15.92 4.18 15.1908 4.18 14.28V10.08Z" fill="#E01E5A"/>
    <path d="M5.82 3.28C4.90923 3.28 4.18 2.55077 4.18 1.64C4.18 0.729231 4.90923 0 5.82 0C6.73077 0 7.46 0.729231 7.46 1.64V3.28H5.82Z" fill="#36C5F0"/>
    <path d="M5.82 4.1C6.73077 4.1 7.46 4.82923 7.46 5.74C7.46 6.65077 6.73077 7.38 5.82 7.38H1.64C0.729231 7.38 0 6.65077 0 5.74C0 4.82923 0.729231 4.1 1.64 4.1H5.82Z" fill="#36C5F0"/>
    <path d="M12.64 5.74C12.64 4.82923 13.3692 4.1 14.28 4.1C15.1908 4.1 15.92 4.82923 15.92 5.74C15.92 6.65077 15.1908 7.38 14.28 7.38H12.64V5.74Z" fill="#2EB67D"/>
    <path d="M11.82 5.74C11.82 6.65077 11.0908 7.38 10.18 7.38C9.26923 7.38 8.54 6.65077 8.54 5.74V1.64C8.54 0.729231 9.26923 0 10.18 0C11.0908 0 11.82 0.729231 11.82 1.64V5.74Z" fill="#2EB67D"/>
    <path d="M10.18 12.64C11.0908 12.64 11.82 13.3692 11.82 14.28C11.82 15.1908 11.0908 15.92 10.18 15.92C9.26923 15.92 8.54 15.1908 8.54 14.28V12.64H10.18Z" fill="#ECB22E"/>
    <path d="M10.18 11.82C9.26923 11.82 8.54 11.0908 8.54 10.18C8.54 9.26923 9.26923 8.54 10.18 8.54H14.36C15.2708 8.54 16 9.26923 16 10.18C16 11.0908 15.2708 11.82 14.36 11.82H10.18Z" fill="#ECB22E"/>
  </svg>
);

const JiraIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M15.5447 7.56533L8.61333 0.633333L8 0L2.64 5.36L0.455333 7.56533C0.164 7.85667 0.164 8.32667 0.455333 8.618L4.83867 13L8 16.1613L13.36 10.8013L13.4453 10.716L15.5447 8.61667C15.8373 8.32533 15.8373 7.85667 15.5447 7.56533ZM8 10.3613L5.63867 8L8 5.63867L10.3613 8L8 10.3613Z" fill="#2684FF"/>
  </svg>
);

const NotionIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path fillRule="evenodd" clipRule="evenodd" d="M2.5 1.5C2.5 1.22386 2.72386 1 3 1H11.5858C11.7167 1 11.8423 1.05268 11.9348 1.14645L13.8536 3.06522C13.9473 3.15898 14 3.28461 14 3.41522V14C14 14.5523 13.5523 15 13 15H3C2.44772 15 2 14.5523 2 14V2C2 1.72386 2.22386 1.5 2.5 1.5ZM4 4V5.5H12V4H4ZM4 7V8.5H12V7H4ZM4 10V11.5H9V10H4Z" fill="black"/>
  </svg>
);

const GoogleSheetsIcon = () => (
  <svg width="12" height="16" viewBox="0 0 12 16" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M7.5 0H1.5C0.675 0 0 0.675 0 1.5V14.5C0 15.325 0.675 16 1.5 16H10.5C11.325 16 12 15.325 12 14.5V4.5L7.5 0Z" fill="#0F9D58"/>
    <path d="M7.5 0V4.5H12L7.5 0Z" fill="#87CEAC"/>
    <path d="M2.25 8.25H9.75V13.5H2.25V8.25Z" fill="white"/>
    <path d="M2.25 8.25V13.5H5.625V8.25H2.25ZM6.375 8.25V13.5H9.75V8.25H6.375Z" stroke="#0F9D58" strokeWidth="0.5"/>
  </svg>
);

const ConfluenceIcon = () => (
  <svg width="18" height="16" viewBox="0 0 18 16" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M0.5 12.5C0.5 12.5 1.5 11 3 11C4.5 11 5.5 12.5 7 12.5C8.5 12.5 9.5 11 9.5 11L8.5 8.5C8.5 8.5 7.5 10 6 10C4.5 10 3.5 8.5 2 8.5C0.5 8.5 0 9.5 0 9.5L0.5 12.5Z" fill="#2684FF"/>
    <path d="M17.5 3.5C17.5 3.5 16.5 5 15 5C13.5 5 12.5 3.5 11 3.5C9.5 3.5 8.5 5 8.5 5L9.5 7.5C9.5 7.5 10.5 6 12 6C13.5 6 14.5 7.5 16 7.5C17.5 7.5 18 6.5 18 6.5L17.5 3.5Z" fill="#2684FF"/>
  </svg>
);

const GitHubIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path fillRule="evenodd" clipRule="evenodd" d="M8 0C3.58 0 0 3.58 0 8C0 11.54 2.29 14.53 5.47 15.59C5.87 15.66 6.02 15.42 6.02 15.21C6.02 15.02 6.01 14.39 6.01 13.72C4 14.09 3.48 13.23 3.32 12.78C3.23 12.55 2.84 11.84 2.5 11.65C2.22 11.5 1.82 11.13 2.49 11.12C3.12 11.11 3.57 11.7 3.72 11.94C4.44 13.15 5.59 12.81 6.05 12.6C6.12 12.08 6.33 11.73 6.56 11.53C4.78 11.33 2.92 10.64 2.92 7.58C2.92 6.71 3.23 5.99 3.74 5.43C3.66 5.23 3.38 4.41 3.82 3.31C3.82 3.31 4.49 3.1 6.02 4.13C6.66 3.95 7.34 3.86 8.02 3.86C8.7 3.86 9.38 3.95 10.02 4.13C11.55 3.09 12.22 3.31 12.22 3.31C12.66 4.41 12.38 5.23 12.3 5.43C12.81 5.99 13.12 6.7 13.12 7.58C13.12 10.65 11.25 11.33 9.47 11.53C9.76 11.78 10.01 12.26 10.01 13.01C10.01 14.08 10 14.94 10 15.21C10 15.42 10.15 15.67 10.55 15.59C13.71 14.53 16 11.53 16 8C16 3.58 12.42 0 8 0Z" fill="#1B1F23"/>
  </svg>
);

const iconMap = {
  slack: SlackIcon,
  jira: JiraIcon,
  notion: NotionIcon,
  sheets: GoogleSheetsIcon,
  confluence: ConfluenceIcon,
  github: GitHubIcon,
};

interface SuggestionChipProps {
  text: string;
  icons: Array<'slack' | 'jira' | 'notion' | 'sheets' | 'confluence' | 'github'>;
  onClick?: () => void;
  /** When true, the chip spans the full width of its container (mobile mode) */
  fullWidth?: boolean;
}

export function SuggestionChip({ text, icons, onClick, fullWidth }: SuggestionChipProps) {
  return (
    <Button
      variant="soft"
      size="2"
      color="gray"
      onClick={onClick}
      style={{
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        background: 'var(--olive-2)',
        width: fullWidth ? '100%' : undefined,
        justifyContent: fullWidth ? 'space-between' : undefined,
        textAlign: fullWidth ? 'left' : undefined,
        height: fullWidth ? 'auto' : undefined,
        paddingTop: fullWidth ? 'var(--space-3)' : undefined,
        paddingBottom: fullWidth ? 'var(--space-3)' : undefined,
      }}
    >
      <Flex align="center" gap="2" style={{ flex: fullWidth ? 1 : undefined, minWidth: 0 }}>
        <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
          {icons.map((icon, idx) => {
            const IconComponent = iconMap[icon];
            return <IconComponent key={idx} />;
          })}
        </Flex>
        <Text
          size="1"
          weight="medium"
          style={{
            color: 'var(--slate-11)',
            whiteSpace: fullWidth ? 'normal' : undefined,
            lineHeight: fullWidth ? '1.4' : undefined,
          }}
        >
          {text}
        </Text>
      </Flex>
      {fullWidth && (
        <MaterialIcon
          name="north_east"
          size={14}
          color="var(--slate-9)"
          style={{ flexShrink: 0, marginLeft: 'var(--space-2)' }}
        />
      )}
    </Button>
  );
}
