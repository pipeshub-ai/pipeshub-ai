'use client';

import React, { createContext, useContext, useId } from 'react';
import { Flex, Box, Text, TextField } from '@radix-ui/themes';

export interface SettingsRowProps {
  label: string;
  description?: string;
  children: React.ReactNode;
}

const SettingsRowField = createContext<{ 'aria-labelledby': string; 'aria-describedby'?: string } | null>(null);

/**
 * A row's text field, named by the row's label and described by its description: the visible label is the row's
 * text beside the field, not a `<label>` around it (a row also holds switches and buttons).
 */
export function SettingsRowTextField(props: React.ComponentProps<typeof TextField.Root>) {
  const field = useContext(SettingsRowField);
  return <TextField.Root {...field} {...props} />;
}

export function SettingsRow({ label, description, children }: SettingsRowProps) {
  const id = useId();
  const labelId = `${id}-label`;
  const descriptionId = description ? `${id}-description` : undefined;
  return (
    <Flex
      direction={{ initial: 'column', sm: 'row' }}
      align={{ initial: 'stretch', sm: 'center' }}
      justify="between"
      gap={{ initial: '2', sm: '0' }}
      style={{ width: '100%' }}
    >
      {/* Left: label + description */}
      <Box style={{ flex: 1, minWidth: 0 }}>
        <Text id={labelId} size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
          {label}
        </Text>
        {description && (
          <Text
            id={descriptionId}
            size="1"
            style={{
              color: 'var(--slate-11)',
              display: 'block',
              marginTop: 2,
              lineHeight: '16px',
              fontWeight: 300,
            }}
          >
            {description}
          </Text>
        )}
      </Box>
      {/* Right: input — proportional width matching Figma */}
      <Box width={{ initial: '100%', sm: '38%' }} minWidth={{ initial: '0', sm: '200px' }} style={{ flexShrink: 0 }}>
        <SettingsRowField.Provider value={{ 'aria-labelledby': labelId, 'aria-describedby': descriptionId }}>{children}</SettingsRowField.Provider>
      </Box>
    </Flex>
  );
}
