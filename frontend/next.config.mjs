/** @type {import('next').NextConfig} */
// Static HTML for Electron packaging (see scripts/next-build-electron.mjs).
const electronStaticExport =
    process.env.ELECTRON_STATIC === '1' || process.env.ELECTRON_STATIC_EXPORT === '1';

const nextConfig = {
    ...(electronStaticExport ? { output: 'export' } : {}),
    trailingSlash: true,
    /**
     * Static export does not emit per-slug callback HTML. Rewrites map
     * `/toolsets/oauth/callback/:slug` and `/connectors/oauth/callback/:slug` → the
     * corresponding static callback page so `next dev` matches Netlify `_redirects`.
     * (Rewrites are not applied to `next export` output; production static hosts still need host rules.)
     */
    async rewrites() {
        return [
            { source: '/toolsets/oauth/callback/:slug', destination: '/toolsets/oauth/callback/' },
            { source: '/toolsets/oauth/callback/:slug/', destination: '/toolsets/oauth/callback/' },
            { source: '/connectors/oauth/callback/:slug', destination: '/connectors/oauth/callback/' },
            { source: '/connectors/oauth/callback/:slug/', destination: '/connectors/oauth/callback/' },
            // `/record/<recordId>` URLs can't ship a dynamic `[recordId]` segment
            // under `output: 'export'`, so the build emits a single `/record/` shell.
            // Rewrite every `/record/:id` to that shell for `next dev`; the page reads
            // the id from `window.location.pathname`. Production static hosts get the
            // same behavior from the Node.js backend SPA fallback.
            { source: '/record/:recordId', destination: '/record/' },
            { source: '/record/:recordId/', destination: '/record/' },
        ];
    },
    webpack: (config) => {
        // pdfjs-dist (bundled by react-pdf-highlighter) has a Node.js code path
        // that requires the native 'canvas' module. Stub it out for the browser build.
        config.resolve.alias = {
            ...config.resolve.alias,
            canvas: false,
        };
        return config;
    },
};

export default nextConfig;
