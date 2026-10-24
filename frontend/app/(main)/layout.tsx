import type { Viewport } from 'next';
import "../globals.css"
import 'react-pdf-highlighter/dist/style/PdfHighlighter.css';
import 'react-pdf-highlighter/dist/style/Highlight.css';
import 'react-pdf-highlighter/dist/style/AreaHighlight.css';
import 'react-pdf-highlighter/dist/style/Tip.css';
import 'react-pdf-highlighter/dist/style/MouseSelection.css';
import RootLayout from './root-layout';

// The root layout is a client component, which cannot export `viewport`, so this server file does.
// viewport-fit=cover lets env(safe-area-inset-*) report the notch and home indicator.
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
};

export default RootLayout;
