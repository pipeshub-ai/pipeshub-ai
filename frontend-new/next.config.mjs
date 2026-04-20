/** @type {import('next').NextConfig} */
const nextConfig = {
    // output: 'export',
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
