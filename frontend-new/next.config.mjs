/** @type {import('next').NextConfig} */
const nextConfig = {
    // output: 'export',
    trailingSlash: true,
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
