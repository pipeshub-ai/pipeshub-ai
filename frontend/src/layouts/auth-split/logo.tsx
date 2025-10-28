import type { ComponentProps } from 'react';

import { Logo } from 'src/components/logo';

// ----------------------------------------------------------------------

export function AuthSplitLogo(props: ComponentProps<typeof Logo>) {
  return (
    <Logo variant="wordmark" iconSize={18} textVariant="caption" spacing={1.2} disableLink {...props} />
  );
}
