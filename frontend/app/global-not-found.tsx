import type { Metadata, Viewport } from 'next';
import PublicLayout from './(public)/root-layout';
import NotFound from './(main)/not-found';

// Unmatched URLs belong to neither root layout, so this page brings its own
// <html> (via the public shell: theme, fonts, i18n).
export const metadata: Metadata = { title: 'PipesHub' };

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
};

export default function GlobalNotFound() {
  return (
    <PublicLayout>
      <NotFound />
    </PublicLayout>
  );
}
