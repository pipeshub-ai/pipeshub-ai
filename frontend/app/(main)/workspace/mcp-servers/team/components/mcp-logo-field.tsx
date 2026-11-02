'use client';

import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Flex } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FormField } from '../../../components';
import { useFormFieldControl } from '../../../components/form-field';
import { McpLogoImage } from '../../components';
import { imageFileToLogo, MCP_LOGO_ACCEPT, McpLogoFileError } from '../../logo-file';

interface McpLogoFieldProps {
  /** What the server will show: its saved logo, or the one just picked. */
  logo: string | null;
  onChange: (logo: string | null) => void;
}

/** A custom server's logo: pick an image, or remove it. Saved with the rest of the form. */
export function McpLogoField({ logo, onChange }: McpLogoFieldProps) {
  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  // Reading a file takes a moment: only the latest pick or remove may change the logo.
  const latestAction = useRef(0);

  const pick = async (file: File) => {
    const action = ++latestAction.current;
    setError(null);
    try {
      const logo = await imageFileToLogo(file);
      if (action === latestAction.current) onChange(logo);
    } catch (e) {
      if (action !== latestAction.current) return;
      const problem = e instanceof McpLogoFileError ? e.problem : 'unreadable';
      setError(t(`workspace.mcpServers.form.logoErrors.${problem}`));
    }
  };

  return (
    <FormField
      label={t('workspace.mcpServers.form.logo')}
      optional
      description={error ? undefined : t('workspace.mcpServers.form.logoHint')}
      error={error ?? undefined}
    >
      <LogoControls
        logo={logo}
        onPick={(file) => void pick(file)}
        onRemove={() => {
          latestAction.current++;
          setError(null);
          onChange(null);
        }}
      />
    </FormField>
  );
}

function LogoControls({ logo, onPick, onRemove }: { logo: string | null; onPick: (file: File) => void; onRemove: () => void }) {
  const { t } = useTranslation();
  const control = useFormFieldControl();
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <Flex align="center" gap="3">
      <Flex
        align="center"
        justify="center"
        style={{
          width: 40,
          height: 40,
          borderRadius: 'var(--radius-2)',
          backgroundColor: 'var(--gray-a3)',
          flexShrink: 0,
        }}
      >
        {logo ? <McpLogoImage src={logo} size={24} /> : <MaterialIcon name="image" size={20} color="var(--gray-9)" />}
      </Flex>
      <Button
        {...control}
        type="button"
        variant="soft"
        color="gray"
        size="2"
        className="touch-hit"
        onClick={() => inputRef.current?.click()}
      >
        <MaterialIcon name="upload" size={16} />
        {logo ? t('workspace.mcpServers.form.logoChange') : t('workspace.mcpServers.form.logoUpload')}
      </Button>
      {logo && (
        <Button type="button" variant="ghost" color="red" size="2" className="touch-hit" onClick={onRemove}>
          {t('workspace.mcpServers.form.logoRemove')}
        </Button>
      )}
      <input
        ref={inputRef}
        type="file"
        accept={MCP_LOGO_ACCEPT}
        hidden
        data-testid="mcp-logo-input"
        onChange={(e) => {
          const file = e.target.files?.[0];
          // Cleared so picking the same file again still fires a change.
          e.target.value = '';
          if (file) onPick(file);
        }}
      />
    </Flex>
  );
}
