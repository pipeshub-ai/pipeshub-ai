import path from 'path';
import checker from 'vite-plugin-checker';
import { defineConfig, Plugin } from 'vite';
import react from '@vitejs/plugin-react-swc';

// ----------------------------------------------------------------------

const PORT = 3001;

// Custom plugin to fix NullL10n import issue in react-pdf-highlighter
// This replaces the problematic import with a local constant definition
// No patch file needed - handled at build time
const fixPdfHighlighterPlugin = (): Plugin => {
  return {
    name: 'fix-pdf-highlighter-null-l10n',
    enforce: 'pre',
    transform(code, id) {
      // Only process the PdfHighlighter.js file from react-pdf-highlighter
      if (id.includes('react-pdf-highlighter') && id.includes('PdfHighlighter.js')) {
        // Check if the file contains the problematic import with NullL10n
        // Pattern matches: import { EventBus, NullL10n, PDFLinkService, PDFViewer, } from "pdfjs-dist/legacy/web/pdf_viewer";
        const importPattern = /import\s*{\s*([^}]*NullL10n[^}]*)\s*}\s*from\s*["']pdfjs-dist\/legacy\/web\/pdf_viewer["'];?/;
        
        if (importPattern.test(code)) {
          const fixedCode = code.replace(importPattern, (match, imports) => {
            // Remove NullL10n from the import list, handling various formats
            const cleanedImports = imports
              .split(',')
              .map((imp: string) => imp.trim())
              .filter((imp: string) => imp && !imp.includes('NullL10n'))
              .join(', ');
            
            // Return the cleaned import followed by the NullL10n constant definition
            return `import { ${cleanedImports} } from "pdfjs-dist/legacy/web/pdf_viewer";\n// NullL10n was removed in pdfjs-dist 4.x, using empty object as replacement\nconst NullL10n = {};`;
          });
          
          return {
            code: fixedCode,
            map: null, // No source map for this transformation
          };
        }
      }
      return null;
    },
  };
};

export default defineConfig({
  plugins: [
    react(),
    fixPdfHighlighterPlugin(),
    checker({
      typescript: true,
      eslint: {
        lintCommand: 'eslint "./src/**/*.{js,jsx,ts,tsx}"',
        dev: { logLevel: ['error'] },
      },
      overlay: {
        position: 'tl',
        initialIsOpen: false,
      },
    }),
  ],
  resolve: {
    alias: [
      {
        find: /^~(.+)/,
        replacement: path.join(process.cwd(), 'node_modules/$1'),
      },
      {
        find: /^src(.+)/,
        replacement: path.join(process.cwd(), 'src/$1'),
      },
    ],
  },
  server: { port: PORT, host: true },
  preview: { port: PORT, host: true },
});
