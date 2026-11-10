import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';

import { KbNodeNameIcon } from '../kb-node-name-icon';

afterEach(() => {
  cleanup();
});

describe('KbNodeNameIcon file types', () => {
  it.each(['yaml', 'yml', 'YAML'])('All Records shows the YAML icon for .%s', (extension) => {
    const { container } = render(
      <KbNodeNameIcon isKnowledgeHub nodeType="record" extension={extension} name="api-action-sample" />,
    );

    expect(container.querySelector('clipPath#yaml_c0')).not.toBeNull();
    expect(container.textContent).not.toContain('description');
  });

  it.each(['application/x-yaml', 'text/x-yaml', 'application/yaml', 'text/yaml'])(
    'a record with no extension shows the YAML icon from its %s type',
    (mimeType) => {
      const { container } = render(
        <KbNodeNameIcon isKnowledgeHub nodeType="record" extension={null} mimeType={mimeType} name="api-action-sample" />,
      );

      expect(container.querySelector('clipPath#yaml_c0')).not.toBeNull();
    },
  );

  it('a collection shows the YAML icon too', () => {
    const { container } = render(
      <KbNodeNameIcon isKnowledgeHub={false} legacyType="file" legacyFileType="yml" name="api-action-sample" />,
    );

    expect(container.querySelector('clipPath#yaml_c0')).not.toBeNull();
  });
});
