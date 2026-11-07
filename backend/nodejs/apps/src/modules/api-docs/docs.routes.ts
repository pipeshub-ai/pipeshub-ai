/**
 * API Documentation Routes
 * Serves the API documentation HTML UI
 */
import { Router, Request, Response } from 'express';
import { Container } from 'inversify';
import helmet from 'helmet';
import { ApiDocsService } from './docs.service';

export function createApiDocsRouter(container: Container): Router {
  const router = Router();
  const apiDocsService = container.get<ApiDocsService>(ApiDocsService);

  // The page uses inline scripts and handlers, and "Try it" calls whatever base URL the reader enters.
  router.use(
    helmet.contentSecurityPolicy({
      directives: {
        scriptSrc: ["'self'", "'unsafe-inline'"],
        scriptSrcAttr: ["'unsafe-inline'"],
        connectSrc: ["'self'", 'http:', 'https:'],
        upgradeInsecureRequests: null,
      },
    }),
  );

  /**
   * Health check endpoint for the docs service
   */
  router.get('/health', (_req: Request, res: Response) => {
    res.json({ status: 'ok', service: 'api-docs' });
  });

  /**
   * Get unified API documentation data
   */
  router.get('/json', (_req: Request, res: Response) => {
    try {
      const unifiedDocs = apiDocsService.getUnifiedDocs();
      res.json(unifiedDocs);
    } catch (error) {
      console.error('Error in /json:', error);
      res.status(500).json({
        error: 'Failed to load API documentation',
        message: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  });

  /**
   * Serve the custom API documentation UI
   * This handles both / and any sub-paths for SPA routing
   */
  router.get('*', (_req: Request, res: Response) => {
    res.send(getDocumentationHtml());
  });

  return router;
}

/**
 * Generate the HTML for the custom API documentation UI
 */
function getDocumentationHtml(): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>PipesHub API Documentation</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <link href="https://fonts.googleapis.com/icon?family=Material+Icons+Outlined" rel="stylesheet">
  <style>
    /* PipesHub theme: the dashboard's Radix olive grays and emerald accent (frontend/app/globals.css). */
    :root {
      --gray-1: #fcfdfc; --gray-2: #f8faf8; --gray-3: #eff1ef; --gray-4: #e7e9e7; --gray-5: #dfe2df;
      --gray-6: #d7dad7; --gray-7: #cccfcc; --gray-8: #b9bcb8; --gray-9: #898e87; --gray-11: #60655f; --gray-12: #1d211c;
      --accent-3: #d4f7eb; --accent-6: #7edabb; --accent-9: #047857; --accent-10: #10674c; --accent-11: #035e44;
      --accent-a3: rgba(4, 120, 87, 0.13); --accent-a4: rgba(4, 120, 87, 0.22);
      --red-3: #feebec; --red-6: #fdbdbe; --red-9: #e5484d; --red-11: #ce2c31;
      --amber-3: #fff7c2; --amber-6: #f3d673; --amber-11: #ab6400;
      --blue-3: #e6f4fe; --blue-6: #acd8fc; --blue-11: #0d74ce;
      --violet-3: #f4f0fe; --violet-11: #6550b9;
      --orange-3: #ffefd6; --orange-11: #cc4e00;

      --bg: #ffffff;
      --bg-sidebar: var(--gray-1);
      --bg-panel: var(--gray-2);
      --bg-hover: var(--gray-3);
      --bg-active: var(--gray-4);
      --surface: #ffffff;
      --border: var(--gray-4);
      --border-strong: var(--gray-6);
      --text: var(--gray-12);
      --text-2: var(--gray-11);
      --text-3: var(--gray-9);
      --code-bg: var(--gray-2);
      --shadow: 0 1px 2px rgba(0, 0, 0, 0.05), 0 2px 8px rgba(0, 0, 0, 0.04);
      --select-chevron: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2360655f' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");

      --font-sans: 'Manrope', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'JetBrains Mono', monospace;
      --radius-1: 3px; --radius-2: 4px; --radius-3: 6px; --radius-4: 8px; --radius-5: 12px;
      color-scheme: light;
    }

    [data-theme="dark"] {
      --gray-1: #111210; --gray-2: #181917; --gray-3: #212220; --gray-4: #282a27; --gray-5: #2f312e;
      --gray-6: #383a36; --gray-7: #454843; --gray-8: #5c625b; --gray-9: #687066; --gray-11: #afb5ad; --gray-12: #eceeec;
      --accent-3: #0c3126; --accent-6: #145c49; --accent-10: #17906c; --accent-11: #3dd9a0;
      --accent-a3: rgba(0, 255, 162, 0.07); --accent-a4: rgba(0, 255, 170, 0.21);
      --red-3: #3b1219; --red-6: #72232d; --red-11: #ff9592;
      --amber-3: #302008; --amber-6: #5c3d05; --amber-11: #ffca16;
      --blue-3: #0d2847; --blue-6: #104d87; --blue-11: #70b8ff;
      --violet-3: #291f43; --violet-11: #baa7ff;
      --orange-3: #331e0b; --orange-11: #ffa057;

      --bg: var(--gray-1);
      --bg-sidebar: var(--gray-1);
      --surface: rgba(0, 0, 0, 0.25);
      --code-bg: #0c0d0c;
      --shadow: 0 1px 2px rgba(0, 0, 0, 0.4), 0 2px 8px rgba(0, 0, 0, 0.3);
      --select-chevron: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23afb5ad' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
      color-scheme: dark;
    }

    * { margin: 0; padding: 0; box-sizing: border-box; }

    html, body { height: 100%; }

    body {
      font-family: var(--font-sans);
      font-size: 14px;
      line-height: 1.5;
      background: var(--bg);
      color: var(--text);
      -webkit-font-smoothing: antialiased;
    }

    button, input, select, textarea { font: inherit; color: inherit; }

    code, pre { font-family: var(--font-mono); }

    code, pre, input, select, textarea { font-variant-ligatures: none; }

    a { color: var(--accent-11); }

    .material-icons-outlined {
      font-size: 18px;
      line-height: 1;
      user-select: none;
      flex-shrink: 0;
    }

    :focus-visible { outline: 2px solid var(--accent-9); outline-offset: 1px; }

    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--gray-6); border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--gray-8); }

    /* Layout */
    .app-container { display: flex; height: 100vh; overflow: hidden; }

    .sidebar {
      width: 280px;
      min-width: 220px;
      max-width: 500px;
      background: var(--bg-sidebar);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
    }

    .sidebar-backdrop { display: none; }

    .resize-handle {
      width: 6px;
      margin: 0 -3px;
      cursor: col-resize;
      flex-shrink: 0;
      position: relative;
      z-index: 10;
      transition: background 0.15s;
    }

    .resize-handle:hover, .resize-handle.dragging { background: var(--accent-a4); }

    body.resizing { user-select: none; cursor: col-resize; }
    body.resizing * { cursor: col-resize !important; }

    /* Same height as .content-header so the two bottom borders line up. */
    .sidebar-header {
      display: flex;
      align-items: center;
      height: 56px;
      padding: 0 16px;
      border-bottom: 1px solid var(--border);
      flex-shrink: 0;
    }

    .sidebar-header .logo { flex: 1; min-width: 0; }

    .logo {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 700;
      font-size: 16px;
      color: var(--text);
      text-decoration: none;
    }

    .logo-icon {
      width: 28px;
      height: 28px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--accent-9);
    }

    .logo-icon svg { width: 22px; height: 22px; }

    [data-theme="dark"] .logo-icon { color: var(--accent-11); }

    .logo-tag {
      margin-left: auto;
      padding: 2px 8px;
      font-size: 11px;
      font-weight: 600;
      color: var(--accent-11);
      background: var(--accent-a3);
      border-radius: 999px;
    }

    .search-box { padding: 12px 16px 4px; position: relative; flex-shrink: 0; }

    .search-input {
      width: 100%;
      height: 32px;
      padding: 0 10px 0 32px;
      border: 1px solid var(--border-strong);
      border-radius: var(--radius-3);
      font-size: 13px;
      background: var(--surface);
      outline: none;
      transition: border-color 0.15s, box-shadow 0.15s;
    }

    .search-input::placeholder { color: var(--text-3); }

    .search-input:focus { border-color: var(--accent-9); box-shadow: 0 0 0 3px var(--accent-a3); }

    .search-icon {
      position: absolute;
      left: 25px;
      top: 28px;
      transform: translateY(-50%);
      color: var(--text-3);
      pointer-events: none;
    }

    .sidebar-nav { flex: 1; overflow-y: auto; padding: 8px; }

    .nav-category + .nav-category { margin-top: 12px; }

    .category-header {
      padding: 8px 8px 4px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-3);
    }

    .nav-item {
      display: flex;
      align-items: center;
      gap: 10px;
      min-height: 32px;
      padding: 0 8px;
      border-radius: var(--radius-3);
      cursor: pointer;
      color: var(--text-2);
      font-size: 14px;
      font-weight: 500;
      text-decoration: none;
      transition: background 0.12s, color 0.12s;
    }

    .nav-item:hover { background: var(--bg-hover); color: var(--text); }

    .nav-item.active { background: var(--accent-a3); color: var(--accent-11); }

    .nav-item-icon { display: flex; align-items: center; justify-content: center; width: 20px; }

    .nav-item-chevron { margin-left: auto; color: var(--text-3); transition: transform 0.15s; }

    .nav-item.active .nav-item-chevron { transform: rotate(90deg); color: var(--accent-11); }

    .endpoint-list { padding: 2px 0 6px 18px; margin-left: 10px; border-left: 1px solid var(--border); }

    .tag-section + .tag-section { margin-top: 1px; }

    .tag-header {
      display: flex;
      align-items: center;
      gap: 4px;
      min-height: 28px;
      padding: 0 6px 0 4px;
      border-radius: var(--radius-3);
      font-size: 13px;
      font-weight: 500;
      color: var(--text-2);
      cursor: pointer;
      transition: background 0.12s, color 0.12s;
    }

    .tag-header:hover { background: var(--bg-hover); color: var(--text); }

    .tag-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

    .tag-count {
      flex-shrink: 0;
      min-width: 20px;
      padding: 0 6px;
      font-size: 11px;
      font-weight: 600;
      line-height: 18px;
      text-align: center;
      color: var(--text-2);
      background: var(--bg-active);
      border-radius: 999px;
    }

    .tag-chevron { color: var(--text-3); transition: transform 0.15s; }

    .tag-endpoints { display: none; padding: 2px 0 4px; }

    .tag-section.expanded .tag-endpoints { display: block; }

    .tag-section.expanded > .tag-header { color: var(--text); }

    .tag-section.expanded .tag-chevron { transform: rotate(90deg); }

    .endpoint-item {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      padding: 5px 6px 5px 8px;
      border-radius: var(--radius-3);
      cursor: pointer;
      font-size: 13px;
      line-height: 18px;
      color: var(--text-2);
      transition: background 0.12s, color 0.12s;
    }

    .endpoint-item:hover { background: var(--bg-hover); color: var(--text); }

    .endpoint-item.active { background: var(--accent-a3); color: var(--text); }

    .endpoint-item .method-badge { width: 46px; margin-top: 1px; }

    .sidebar-empty {
      padding: 24px 12px;
      text-align: center;
      font-size: 13px;
      color: var(--text-3);
    }

    /* Method badges: Radix soft badges */
    .method-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      height: 18px;
      padding: 0 5px;
      border-radius: var(--radius-2);
      font-family: var(--font-mono);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }

    .method-get { background: var(--accent-a3); color: var(--accent-11); }
    .method-post { background: var(--blue-3); color: var(--blue-11); }
    .method-put { background: var(--amber-3); color: var(--amber-11); }
    .method-patch { background: var(--violet-3); color: var(--violet-11); }
    .method-delete { background: var(--red-3); color: var(--red-11); }
    .method-head, .method-options { background: var(--bg-active); color: var(--text-2); }

    .method-badge.large { height: 26px; padding: 0 10px; font-size: 13px; border-radius: var(--radius-3); }

    /* Main content */
    .main-content { flex: 1; display: flex; flex-direction: column; min-width: 0; overflow: hidden; }

    .content-header {
      display: flex;
      align-items: center;
      gap: 12px;
      height: 56px;
      padding: 0 20px;
      border-bottom: 1px solid var(--border);
      flex-shrink: 0;
    }

    .breadcrumb {
      display: flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
      flex: 1;
      font-size: 13px;
      color: var(--text-3);
      white-space: nowrap;
      overflow: hidden;
    }

    .breadcrumb > span { overflow: hidden; text-overflow: ellipsis; }

    .breadcrumb-separator { color: var(--gray-7); flex-shrink: 0; }

    .breadcrumb-current { color: var(--text); font-weight: 600; }

    .breadcrumb-link { cursor: pointer; flex-shrink: 0; transition: color 0.12s; }

    .breadcrumb-link:hover { color: var(--accent-11); }

    .icon-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      flex-shrink: 0;
      background: transparent;
      border: 1px solid var(--border-strong);
      border-radius: var(--radius-3);
      color: var(--text-2);
      cursor: pointer;
      transition: background 0.12s, color 0.12s;
    }

    .icon-btn:hover { background: var(--bg-hover); color: var(--text); }

    .menu-toggle { display: none; }

    .theme-toggle .sun-icon { display: none; }
    [data-theme="dark"] .theme-toggle .sun-icon { display: inline; }
    [data-theme="dark"] .theme-toggle .moon-icon { display: none; }

    .content-body { flex: 1; display: flex; min-height: 0; overflow: hidden; }

    /* Documentation panel */
    .doc-panel { flex: 1; min-width: 0; overflow-y: auto; padding: 32px 40px 64px; }

    .doc-panel > * { max-width: 920px; }

    .endpoint-header { margin-bottom: 8px; }

    .endpoint-title { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

    .endpoint-title h1 { font-size: 24px; font-weight: 700; line-height: 1.3; letter-spacing: -0.01em; }

    .endpoint-path {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 14px;
      padding: 8px 8px 8px 12px;
      font-family: var(--font-mono);
      font-size: 13px;
      color: var(--text);
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: var(--radius-3);
      word-break: break-all;
    }

    .endpoint-path-base { color: var(--text-3); }

    .endpoint-path .copy-btn { margin-left: auto; }

    /* Rendered Markdown from the spec */
    .md { color: var(--text-2); font-size: 14px; line-height: 1.65; overflow-wrap: anywhere; }

    .md > * + * { margin-top: 10px; }

    .md strong, .md b { color: var(--text); font-weight: 600; }

    .md h3, .md h4, .md h5 { color: var(--text); font-size: 14px; font-weight: 700; margin-top: 16px; }

    .md ul, .md ol { padding-left: 20px; }

    .md li + li { margin-top: 4px; }

    .md code {
      font-size: 0.88em;
      padding: 1px 5px;
      color: var(--text);
      background: var(--bg-hover);
      border: 1px solid var(--border);
      border-radius: var(--radius-2);
      overflow-wrap: anywhere;
    }

    .md pre {
      padding: 12px 14px;
      font-size: 12px;
      line-height: 1.6;
      color: var(--text);
      background: var(--code-bg);
      border: 1px solid var(--border);
      border-radius: var(--radius-4);
      overflow-x: auto;
    }

    .md pre code { padding: 0; background: none; border: 0; font-size: inherit; overflow-wrap: normal; }

    .md a { color: var(--accent-11); text-decoration: underline; text-underline-offset: 2px; }

    .md.small { font-size: 12.5px; line-height: 1.55; }

    .md.small > * + * { margin-top: 6px; }

    .endpoint-description { margin-top: 20px; }

    /* Sections */
    .doc-section { margin-top: 36px; }

    .section-title {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 16px;
      font-weight: 700;
      margin-bottom: 12px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--border);
    }

    .section-subtitle { font-size: 12px; font-weight: 500; color: var(--text-3); }

    .content-type {
      margin-left: auto;
      padding: 2px 8px;
      font-family: var(--font-mono);
      font-size: 11px;
      font-weight: 500;
      color: var(--text-2);
      background: var(--bg-hover);
      border-radius: 999px;
    }

    .section-note { margin-bottom: 12px; }

    /* Parameters table */
    .params-table {
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      border: 1px solid var(--border);
      border-radius: var(--radius-4);
      overflow: hidden;
      font-size: 13px;
    }

    .params-table th, .params-table td {
      padding: 10px 14px;
      text-align: left;
      vertical-align: top;
      border-bottom: 1px solid var(--border);
    }

    .params-table tr:last-child td { border-bottom: none; }

    .params-table th {
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-3);
      background: var(--bg-panel);
    }

    .param-name { font-family: var(--font-mono); font-size: 13px; font-weight: 600; color: var(--text); }

    .param-in { display: block; margin-top: 2px; font-size: 11px; color: var(--text-3); }

    .param-type { font-family: var(--font-mono); font-size: 12px; color: var(--violet-11); }

    .param-required, .schema-field-required {
      display: inline-block;
      margin-left: 6px;
      padding: 0 5px;
      font-size: 10px;
      font-weight: 600;
      line-height: 16px;
      vertical-align: 1px;
      color: var(--red-11);
      background: var(--red-3);
      border-radius: var(--radius-2);
    }

    /* Responses */
    .response-item {
      margin-bottom: 10px;
      border: 1px solid var(--border);
      border-radius: var(--radius-4);
      overflow: hidden;
      background: var(--bg);
    }

    .response-header {
      display: flex;
      align-items: flex-start;
      gap: 12px;
      padding: 12px 14px;
      background: var(--bg-panel);
    }

    .response-item > .schema-block { margin: 0; border: 0; border-top: 1px solid var(--border); border-radius: 0; }

    .response-code {
      flex-shrink: 0;
      min-width: 44px;
      padding: 1px 8px;
      font-family: var(--font-mono);
      font-size: 12px;
      font-weight: 700;
      line-height: 20px;
      text-align: center;
      border-radius: var(--radius-2);
    }

    .response-header .md { flex: 1; min-width: 0; }

    .response-code-2xx { background: var(--accent-a3); color: var(--accent-11); }
    .response-code-3xx { background: var(--blue-3); color: var(--blue-11); }
    .response-code-4xx { background: var(--amber-3); color: var(--amber-11); }
    .response-code-5xx { background: var(--red-3); color: var(--red-11); }
    .response-code-default { background: var(--bg-active); color: var(--text-2); }

    /* Tag overview: endpoint rows */
    .endpoint-row {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      cursor: pointer;
      transition: background 0.12s;
    }

    .endpoint-row:last-child { border-bottom: none; }

    .endpoint-row:hover { background: var(--bg-hover); }

    .endpoint-row .method-badge { width: 56px; }

    .endpoint-row-summary { flex: 1; min-width: 0; font-weight: 600; color: var(--text); }

    .endpoint-row-path {
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--text-3);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 45%;
    }

    .endpoint-rows { border: 1px solid var(--border); border-radius: var(--radius-4); overflow: hidden; }

    /* Code / Try It panel */
    .code-panel {
      width: 480px;
      min-width: 340px;
      max-width: 800px;
      background: var(--bg-sidebar);
      border-left: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
      min-height: 0;
    }

    .code-panel-header {
      display: flex;
      align-items: center;
      gap: 2px;
      height: 48px;
      padding: 0 12px;
      border-bottom: 1px solid var(--border);
      overflow-x: auto;
      flex-shrink: 0;
    }

    .code-tab {
      position: relative;
      display: flex;
      align-items: center;
      height: 100%;
      padding: 0 10px;
      font-size: 13px;
      font-weight: 500;
      color: var(--text-2);
      cursor: pointer;
      white-space: nowrap;
      transition: color 0.12s;
    }

    .code-tab:hover { color: var(--text); }

    .code-tab.active { color: var(--text); font-weight: 600; }

    .code-tab.active::after {
      content: '';
      position: absolute;
      left: 8px;
      right: 8px;
      bottom: -1px;
      height: 2px;
      background: var(--accent-9);
      border-radius: 2px;
    }

    [data-theme="dark"] .code-tab.active::after { background: var(--accent-11); }

    .code-content { flex: 1; overflow: auto; padding: 16px; }

    .code-block {
      background: var(--code-bg);
      border: 1px solid var(--border);
      border-radius: var(--radius-4);
      overflow: hidden;
    }

    .code-block-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 6px 8px 6px 14px;
      border-bottom: 1px solid var(--border);
      background: var(--bg-panel);
    }

    .code-block-title { font-size: 12px; font-weight: 600; color: var(--text-2); }

    .code-block pre {
      margin: 0;
      padding: 14px;
      font-size: 12.5px;
      line-height: 1.6;
      color: var(--text);
      overflow-x: auto;
      white-space: pre;
    }

    .code-block .raw-json-textarea { border: 0; border-radius: 0; min-height: 320px; background: transparent; }

    .code-block-footer { padding: 12px; border-top: 1px solid var(--border); }

    .copy-btn {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      height: 26px;
      padding: 0 8px;
      background: none;
      border: none;
      border-radius: var(--radius-2);
      color: var(--text-2);
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.12s, color 0.12s;
    }

    .copy-btn .material-icons-outlined { font-size: 15px; }

    .copy-btn:hover { background: var(--bg-hover); color: var(--text); }

    /* Try It form */
    .try-it-section { margin-bottom: 20px; }

    .try-it-label {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-3);
    }

    .try-it-input, .try-it-textarea, .raw-json-textarea,
    .body-field-input input, .body-field-input select, .body-field-input textarea {
      width: 100%;
      padding: 7px 10px;
      background: var(--surface);
      border: 1px solid var(--border-strong);
      border-radius: var(--radius-3);
      font-family: var(--font-mono);
      font-size: 13px;
      color: var(--text);
      outline: none;
      transition: border-color 0.15s, box-shadow 0.15s;
    }

    .try-it-input, .body-field-input input, .body-field-input select { height: 34px; }

    .try-it-input:focus, .try-it-textarea:focus, .raw-json-textarea:focus,
    .body-field-input input:focus, .body-field-input select:focus, .body-field-input textarea:focus {
      border-color: var(--accent-9);
      box-shadow: 0 0 0 3px var(--accent-a3);
    }

    .try-it-input::placeholder, .raw-json-textarea::placeholder,
    .body-field-input input::placeholder, .body-field-input textarea::placeholder { color: var(--text-3); }

    .try-it-textarea, .raw-json-textarea, .body-field-input textarea { resize: vertical; line-height: 1.55; }

    .raw-json-textarea { min-height: 220px; }

    .body-field-input textarea { min-height: 84px; }

    .body-field-input select, select.try-it-input {
      appearance: none;
      cursor: pointer;
      padding-right: 32px;
      background-image: var(--select-chevron);
      background-repeat: no-repeat;
      background-position: right 10px center;
    }

    .raw-json-textarea.error { border-color: var(--red-9); }

    .raw-json-error { margin-top: 6px; font-size: 12px; color: var(--red-11); }

    .input-mode-toggle {
      display: flex;
      margin-bottom: 12px;
      padding: 2px;
      background: var(--bg-active);
      border-radius: var(--radius-3);
    }

    .input-mode-btn {
      flex: 1;
      height: 28px;
      background: transparent;
      border: none;
      border-radius: var(--radius-2);
      color: var(--text-2);
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.12s, color 0.12s;
    }

    .input-mode-btn:hover { color: var(--text); }

    .input-mode-btn.active { background: var(--bg); color: var(--text); box-shadow: var(--shadow); }

    [data-theme="dark"] .input-mode-btn.active { background: var(--gray-6); }

    .param-input-group { margin-bottom: 12px; }

    .param-input-label {
      display: flex;
      align-items: baseline;
      gap: 6px;
      margin-bottom: 5px;
      font-family: var(--font-mono);
      font-size: 12.5px;
      font-weight: 600;
      color: var(--text);
      overflow-wrap: anywhere;
    }

    .param-input-label .field-type { font-weight: 400; color: var(--text-3); font-size: 11px; }

    .required { color: var(--red-11); margin-left: 2px; }

    .body-field-group { display: flex; flex-direction: column; gap: 14px; }

    .body-field-label {
      display: flex;
      align-items: baseline;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 5px;
      font-family: var(--font-mono);
      font-size: 12.5px;
      font-weight: 600;
      color: var(--text);
      overflow-wrap: anywhere;
    }

    .body-field-label .field-type { font-size: 11px; font-weight: 400; color: var(--text-3); }

    .body-field-desc { margin-top: 5px; }

    .body-field-desc.md { font-size: 12px; line-height: 1.5; color: var(--text-3); }

    .nested-object {
      padding: 12px 0 2px 12px;
      border-left: 2px solid var(--accent-6);
    }

    .nested-object-label {
      margin-bottom: 10px;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--text-3);
    }

    .array-item { display: flex; align-items: flex-start; gap: 6px; margin-bottom: 6px; }

    .array-item input, .array-item textarea { flex: 1; min-width: 0; }

    .array-item textarea { min-height: 150px; }

    .array-add-btn, .array-remove-btn, .secondary-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
      height: 30px;
      padding: 0 10px;
      background: var(--bg);
      border: 1px solid var(--border-strong);
      border-radius: var(--radius-3);
      color: var(--text);
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.12s, color 0.12s, border-color 0.12s;
    }

    .array-add-btn .material-icons-outlined, .array-remove-btn .material-icons-outlined { font-size: 16px; }

    .array-add-btn:hover, .secondary-btn:hover { background: var(--bg-hover); }

    .array-remove-btn { width: 34px; height: 34px; padding: 0; color: var(--text-2); }

    .array-remove-btn:hover { color: var(--red-11); border-color: var(--red-6); background: var(--red-3); }

    /* File upload */
    .file-upload-input {
      width: 100%;
      padding: 6px;
      background: var(--surface);
      border: 1px dashed var(--border-strong);
      border-radius: var(--radius-4);
      font-size: 13px;
      color: var(--text-2);
      cursor: pointer;
      transition: border-color 0.15s, background 0.15s;
    }

    .body-field-input input.file-upload-input { height: auto; font-family: var(--font-sans); }

    .file-upload-input:hover { border-color: var(--accent-9); background: var(--accent-a3); }

    .file-upload-input::file-selector-button {
      height: 30px;
      margin-right: 12px;
      padding: 0 12px;
      background: var(--bg);
      border: 1px solid var(--border-strong);
      border-radius: var(--radius-3);
      color: var(--text);
      font: inherit;
      font-weight: 600;
      cursor: pointer;
    }

    .file-selected-info {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-top: 8px;
      padding: 6px 6px 6px 10px;
      font-size: 12px;
      color: var(--accent-11);
      background: var(--accent-a3);
      border-radius: var(--radius-3);
    }

    .file-selected-info .file-name { font-family: var(--font-mono); word-break: break-all; }

    .file-selected-info .file-size { margin-left: 6px; color: var(--text-2); }

    .file-clear-btn {
      display: inline-flex;
      background: transparent;
      border: none;
      color: var(--text-2);
      cursor: pointer;
      border-radius: var(--radius-2);
    }

    .file-clear-btn:hover { color: var(--red-11); }

    /* Buttons */
    .execute-btn, .primary-btn {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      width: 100%;
      height: 36px;
      background: var(--accent-9);
      color: #ffffff;
      border: none;
      border-radius: var(--radius-3);
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.12s;
    }

    .execute-btn:hover, .primary-btn:hover { background: var(--accent-10); }

    .execute-btn:disabled { opacity: 0.6; cursor: progress; }

    /* Live response */
    .response-section { margin-top: 20px; scroll-margin-top: 16px; }

    .response-pre {
      max-height: 420px;
      overflow: auto;
      padding: 12px 14px;
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: break-word;
      color: var(--text);
      background: var(--code-bg);
      border: 1px solid var(--border);
      border-radius: var(--radius-4);
    }

    .response-status {
      padding: 1px 8px;
      font-family: var(--font-mono);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: none;
      border-radius: var(--radius-2);
    }

    .response-status.success { background: var(--accent-a3); color: var(--accent-11); }

    .response-status.error { background: var(--red-3); color: var(--red-11); }

    .response-time { margin-left: auto; font-weight: 500; text-transform: none; letter-spacing: 0; }

    .response-headers-section {
      margin-top: 10px;
      border: 1px solid var(--border);
      border-radius: var(--radius-4);
      overflow: hidden;
    }

    .response-headers-toggle {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 12px;
      background: var(--bg-panel);
      cursor: pointer;
      user-select: none;
    }

    .response-headers-toggle:hover { background: var(--bg-hover); }

    .response-headers-toggle-left {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      font-weight: 600;
      color: var(--text-2);
    }

    .response-headers-toggle-icon { transition: transform 0.15s; }

    .response-headers-section.expanded .response-headers-toggle-icon { transform: rotate(90deg); }

    .response-headers-count {
      padding: 0 7px;
      font-size: 11px;
      font-weight: 600;
      line-height: 18px;
      color: var(--text-2);
      background: var(--bg-active);
      border-radius: 999px;
    }

    .response-headers-content { display: none; padding: 4px 12px; border-top: 1px solid var(--border); }

    .response-headers-section.expanded .response-headers-content { display: block; }

    .header-row {
      display: flex;
      gap: 12px;
      padding: 6px 0;
      border-bottom: 1px solid var(--border);
      font-family: var(--font-mono);
      font-size: 12px;
    }

    .header-row:last-child { border-bottom: none; }

    .header-name { flex: 0 0 40%; color: var(--text-2); font-weight: 600; word-break: break-all; }

    .header-value { flex: 1; color: var(--text); word-break: break-all; }

    /* Schemas */
    .schema-block {
      margin-top: 8px;
      border: 1px solid var(--border);
      border-radius: var(--radius-4);
      overflow: hidden;
    }

    .schema-header {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 12px;
      background: var(--bg-panel);
      cursor: pointer;
      user-select: none;
      transition: background 0.12s;
    }

    .schema-header:hover { background: var(--bg-hover); }

    .schema-header .material-icons-outlined { color: var(--text-3); transition: transform 0.15s; }

    .schema-block.expanded > .schema-header .material-icons-outlined { transform: rotate(90deg); }

    .schema-name { font-family: var(--font-mono); font-size: 12.5px; font-weight: 600; color: var(--text); }

    .schema-toggle { margin-left: auto; font-size: 12px; color: var(--text-3); }

    .schema-content { display: none; border-top: 1px solid var(--border); overflow-x: auto; }

    .schema-block.expanded > .schema-content { display: block; }

    .schema-table { width: 100%; border-collapse: collapse; font-size: 13px; }

    .schema-table th {
      padding: 8px 12px;
      text-align: left;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-3);
      background: var(--bg-panel);
      border-bottom: 1px solid var(--border);
    }

    .schema-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }

    .schema-table td:first-child { width: 28%; }

    .schema-table td:nth-child(2) { width: 18%; }

    .schema-table tr:last-child > td { border-bottom: none; }

    .schema-table .schema-table { border: 1px solid var(--border); border-radius: var(--radius-3); }

    .schema-field-name { font-family: var(--font-mono); font-size: 12.5px; font-weight: 600; color: var(--text); overflow-wrap: anywhere; }

    .schema-field-type {
      display: inline-block;
      font-family: var(--font-mono);
      font-size: 12px;
      color: var(--violet-11);
      overflow-wrap: anywhere;
    }

    .schema-format-badge {
      display: inline-block;
      margin-left: 4px;
      padding: 0 5px;
      font-family: var(--font-mono);
      font-size: 10px;
      line-height: 16px;
      color: var(--blue-11);
      background: var(--blue-3);
      border-radius: var(--radius-2);
    }

    .schema-field-desc { color: var(--text-2); }

    .schema-meta { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }

    .schema-meta span, .schema-field-enum-value {
      display: inline-block;
      padding: 0 6px;
      font-family: var(--font-mono);
      font-size: 11px;
      line-height: 18px;
      color: var(--text-2);
      background: var(--bg-hover);
      border-radius: var(--radius-2);
      word-break: break-all;
    }

    .schema-meta .schema-field-example { color: var(--accent-11); background: var(--accent-a3); }

    .schema-field-enum { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin-top: 6px; font-size: 12px; color: var(--text-3); }

    .schema-nested { margin-top: 8px; }

    .schema-nested-header {
      display: inline-flex;
      align-items: center;
      gap: 2px;
      padding: 2px 6px 2px 2px;
      font-size: 12px;
      font-weight: 600;
      color: var(--accent-11);
      border-radius: var(--radius-2);
      cursor: pointer;
    }

    .schema-nested-header:hover { background: var(--accent-a3); }

    .schema-nested-toggle { transition: transform 0.15s; }

    .schema-nested-toggle.expanded { transform: rotate(90deg); }

    .schema-nested-content { display: none; margin-top: 8px; }

    .schema-nested-content.expanded { display: block; }

    .schema-oneof-container { margin-top: 8px; }

    .schema-oneof-caption { margin-bottom: 6px; font-size: 11px; color: var(--text-3); }

    .schema-oneof-tabs { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 8px; }

    .schema-oneof-tab {
      padding: 2px 8px;
      font-family: var(--font-mono);
      font-size: 11.5px;
      color: var(--text-2);
      background: var(--bg);
      border: 1px solid var(--border-strong);
      border-radius: 999px;
      cursor: pointer;
    }

    .schema-oneof-tab:hover { color: var(--text); background: var(--bg-hover); }

    .schema-oneof-tab.active { color: var(--accent-11); background: var(--accent-a3); border-color: var(--accent-6); }

    .schema-oneof-content { display: none; }

    .schema-oneof-content.active { display: block; }

    .schema-scalar { padding: 10px 12px; color: var(--text-2); }

    /* Loading */
    .loading { display: flex; align-items: center; justify-content: center; gap: 10px; height: 100%; padding: 24px; color: var(--text-3); }

    .spinner {
      width: 28px;
      height: 28px;
      border: 2px solid var(--border-strong);
      border-top-color: var(--accent-9);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
    }

    @keyframes spin { to { transform: rotate(360deg); } }

    /* Welcome */
    .welcome-screen { padding: 24px 0 8px; }

    .welcome-icon {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 48px;
      height: 48px;
      margin-bottom: 20px;
      color: var(--accent-9);
      background: var(--accent-a3);
      border-radius: var(--radius-5);
    }

    [data-theme="dark"] .welcome-icon { color: var(--accent-11); }

    .welcome-icon svg { width: 28px; height: 28px; }

    .welcome-title { font-size: 28px; font-weight: 700; line-height: 1.25; letter-spacing: -0.015em; margin-bottom: 8px; }

    .welcome-description { max-width: 620px; color: var(--text-2); font-size: 15px; line-height: 1.65; }

    .quickstart { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; margin-top: 28px; }

    .quickstart-card { padding: 16px; border: 1px solid var(--border); border-radius: var(--radius-4); background: var(--bg-panel); }

    .quickstart-card .material-icons-outlined { color: var(--accent-11); font-size: 20px; }

    .quickstart-card h3 { margin: 10px 0 4px; font-size: 14px; font-weight: 700; }

    .quickstart-card p { color: var(--text-2); font-size: 13px; line-height: 1.55; }

    .quickstart-card code { font-size: 12px; padding: 0 4px; background: var(--bg-active); border-radius: var(--radius-2); }

    /* Module overview cards */
    .module-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }

    .module-card {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 14px;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: var(--radius-4);
      cursor: pointer;
      transition: border-color 0.12s, box-shadow 0.12s, background 0.12s;
    }

    .module-card:hover { border-color: var(--accent-6); background: var(--bg-panel); box-shadow: var(--shadow); }

    .module-card-icon {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 36px;
      height: 36px;
      flex-shrink: 0;
      color: var(--accent-11);
      background: var(--accent-a3);
      border-radius: var(--radius-3);
    }

    .module-card-header { flex: 1; min-width: 0; }

    .module-card-title { font-weight: 700; font-size: 14px; overflow-wrap: anywhere; }

    .module-card-meta { margin-top: 2px; font-size: 12px; color: var(--text-3); }

    .module-card-arrow { color: var(--text-3); }

    /* Health */
    .health-section {
      margin-top: 28px;
      padding: 20px;
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: var(--radius-5);
    }

    .health-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }

    .health-title { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 700; }

    .health-title-icon {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 30px;
      height: 30px;
      color: var(--accent-11);
      background: var(--accent-a3);
      border-radius: var(--radius-3);
    }

    .health-refresh-btn { flex-shrink: 0; }

    .health-refresh-btn.loading { pointer-events: none; opacity: 0.7; }

    .health-refresh-btn.loading .material-icons-outlined { animation: spin 0.8s linear infinite; }

    .health-overall {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
    }

    .health-overall-label { font-size: 13px; color: var(--text-2); }

    .health-overall-status { padding: 1px 10px; font-size: 12px; font-weight: 700; line-height: 20px; border-radius: 999px; }

    .health-overall-status.healthy { background: var(--accent-a3); color: var(--accent-11); }
    .health-overall-status.degraded { background: var(--amber-3); color: var(--amber-11); }
    .health-overall-status.unhealthy { background: var(--red-3); color: var(--red-11); }

    .health-timestamp { margin-left: auto; font-size: 12px; color: var(--text-3); }

    .health-services { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 8px; }

    .health-service-card {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: var(--radius-4);
    }

    .health-service-indicator { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }

    .health-service-indicator.healthy { background: var(--accent-9); box-shadow: 0 0 0 3px var(--accent-a3); }
    .health-service-indicator.unhealthy { background: var(--red-9); box-shadow: 0 0 0 3px var(--red-3); }
    .health-service-indicator.unknown { background: var(--gray-8); }

    [data-theme="dark"] .health-service-indicator.healthy { background: var(--accent-11); }

    .health-service-info { flex: 1; min-width: 0; }

    .health-service-name { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    .health-service-details { font-size: 12px; color: var(--text-3); text-transform: capitalize; }

    .health-loading { display: flex; align-items: center; justify-content: center; gap: 10px; padding: 24px; color: var(--text-3); }

    .health-loading .spinner { width: 18px; height: 18px; }

    /* Responsive: below 1100px the Try It panel stacks under the docs */
    @media (max-width: 1100px) {
      .content-body { flex-direction: column; overflow-y: auto; }
      .doc-panel { flex: none; overflow: visible; padding: 24px 24px 32px; }
      .code-panel { width: auto !important; max-width: none; min-width: 0; border-left: 0; border-top: 1px solid var(--border); }
      .code-content { overflow: visible; }
      .resize-handle-left { display: none !important; }
    }

    /* Below 900px the sidebar becomes a drawer */
    @media (max-width: 900px) {
      .sidebar {
        position: fixed;
        top: 0;
        bottom: 0;
        left: 0;
        z-index: 100;
        width: min(320px, 85vw) !important;
        transform: translateX(-100%);
        transition: transform 0.2s ease;
        box-shadow: var(--shadow);
      }
      .sidebar.open { transform: none; }
      .sidebar-backdrop.open { display: block; position: fixed; inset: 0; z-index: 99; background: rgba(0, 0, 0, 0.4); }
      .resize-handle-right { display: none; }
      .menu-toggle { display: inline-flex; }
      .breadcrumb-link, .breadcrumb-separator { display: none; }
      .content-header { padding: 0 12px; }
      .doc-panel { padding: 20px 16px 28px; }
      .code-content { padding: 16px 12px; }
      .endpoint-title h1 { font-size: 20px; }
      .welcome-title { font-size: 24px; }
      .endpoint-row { flex-wrap: wrap; }
      .endpoint-row-path { max-width: 100%; flex-basis: 100%; padding-left: 68px; }
      .schema-table { min-width: 520px; }
    }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { transition: none !important; animation-duration: 0.01ms !important; }
    }
  </style>
  <script>
    // Prevent flash of wrong theme
    (function() {
      const savedTheme = localStorage.getItem('pipeshub_docs_theme');
      if (savedTheme === 'dark') {
        document.documentElement.setAttribute('data-theme', 'dark');
      } else if (!savedTheme && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        document.documentElement.setAttribute('data-theme', 'dark');
      }
    })();
  </script>
</head>
<body>
  <div id="app" class="app-container">
    <aside class="sidebar" id="sidebar">
      <div class="sidebar-header">
        <a href="/api/v1/docs" class="logo">
          <span class="logo-icon"><svg viewBox="0 0 614 614" fill="currentColor" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M110.72 110.72V186.212L186.217 186.212L186.217 110.72Z"/><path d="M110.72 427.786V503.277L186.217 503.277L186.217 427.786Z"/><path d="M427.783 110.72V186.212L503.28 186.212L503.28 110.72Z"/><path d="M427.779 427.786V503.277L503.275 503.277L503.275 427.786Z"/><path d="M306.998 447.914L362.358 503.277L306.998 558.64L251.637 503.277Z"/><path d="M306.998 55.3602L362.358 110.723L306.998 166.085L251.637 110.723Z"/><path d="M306.998 251.642L362.358 307.005L306.998 362.367L251.637 307.005Z"/><path d="M503.275 251.637L558.635 307L503.275 362.363L447.914 307Z"/><path d="M110.72 251.637L166.081 307L110.72 362.363L55.3602 307Z"/></svg></span>
          <span>PipesHub API</span>
          <span class="logo-tag" id="apiVersion"></span>
        </a>
      </div>
      <div class="search-box">
        <span class="material-icons-outlined search-icon" aria-hidden="true">search</span>
        <input type="search" class="search-input" placeholder="Search endpoints, paths, tags" id="searchInput" aria-label="Search endpoints">
      </div>
      <nav class="sidebar-nav" id="sidebarNav">
        <div class="loading">
          <div class="spinner"></div>
        </div>
      </nav>
    </aside>
    <div class="sidebar-backdrop" id="sidebarBackdrop"></div>

    <div class="resize-handle resize-handle-right" id="sidebarResizeHandle"></div>

    <main class="main-content">
      <header class="content-header">
        <button class="icon-btn menu-toggle" id="menuToggle" type="button" aria-label="Open navigation">
          <span class="material-icons-outlined">menu</span>
        </button>
        <div class="breadcrumb" id="breadcrumb">
          <span class="breadcrumb-current">API Reference</span>
        </div>
        <button class="icon-btn theme-toggle" id="themeToggle" type="button" title="Toggle dark mode" aria-label="Toggle dark mode">
          <span class="material-icons-outlined moon-icon">dark_mode</span>
          <span class="material-icons-outlined sun-icon">light_mode</span>
        </button>
      </header>

      <div class="content-body">
        <div class="doc-panel" id="docPanel"></div>

        <div class="resize-handle resize-handle-left" id="codePanelResizeHandle" style="display: none;"></div>

        <div class="code-panel" id="codePanel" style="display: none;">
          <div class="code-panel-header">
            <div class="code-tab active" data-tab="tryit">Try It</div>
            <div class="code-tab" data-tab="curl">cURL</div>
            <div class="code-tab" data-tab="javascript">JavaScript</div>
            <div class="code-tab" data-tab="python">Python</div>
            <div class="code-tab" data-tab="requestbody" id="requestBodyTab" style="display: none;">Request Body</div>
          </div>
          <div class="code-content" id="codeContent">
            <div id="tryItPanel" class="try-it-panel">
              <div class="try-it-section">
                <label class="try-it-label" for="baseUrlInput">Base URL</label>
                <input type="text" id="baseUrlInput" class="try-it-input" placeholder="https://your-pipeshub-host">
              </div>
              <div class="try-it-section">
                <label class="try-it-label" for="authTokenInput">Authorization token</label>
                <input type="password" id="authTokenInput" class="try-it-input" placeholder="Bearer token or personal access token" autocomplete="off">
              </div>
              <div id="headersSection" class="try-it-section" style="display: none;">
                <label class="try-it-label">Headers</label>
                <div id="headersInputs"></div>
              </div>
              <div id="pathParamsSection" class="try-it-section" style="display: none;">
                <label class="try-it-label">Path parameters</label>
                <div id="pathParamsInputs"></div>
              </div>
              <div id="queryParamsSection" class="try-it-section" style="display: none;">
                <label class="try-it-label">Query parameters</label>
                <div id="queryParamsInputs"></div>
              </div>
              <div id="requestBodySection" class="try-it-section" style="display: none;">
                <label class="try-it-label">Request body</label>
                <div class="input-mode-toggle" id="inputModeToggle">
                  <button type="button" class="input-mode-btn active" data-mode="form">Form</button>
                  <button type="button" class="input-mode-btn" data-mode="raw">Raw JSON</button>
                </div>
                <div id="requestBodyInputs"></div>
                <div id="rawJsonInput" style="display: none;">
                  <textarea id="rawJsonTextarea" class="raw-json-textarea" spellcheck="false" placeholder='{"key": "value"}'></textarea>
                  <div id="rawJsonError" class="raw-json-error" style="display: none;"></div>
                </div>
              </div>
              <button id="executeBtn" class="execute-btn" type="button">
                <span class="material-icons-outlined">play_arrow</span>
                Send request
              </button>
              <div id="responseSection" class="try-it-section response-section" style="display: none;">
                <label class="try-it-label">Response <span id="responseStatus"></span><span id="responseTime" class="response-time"></span></label>
                <pre id="responseBody" class="response-pre"></pre>
                <div id="responseHeadersSection" class="response-headers-section">
                  <div class="response-headers-toggle">
                    <div class="response-headers-toggle-left">
                      <span class="material-icons-outlined response-headers-toggle-icon">chevron_right</span>
                      <span>Response headers</span>
                    </div>
                    <span id="responseHeadersCount" class="response-headers-count">0</span>
                  </div>
                  <div id="responseHeadersContent" class="response-headers-content"></div>
                </div>
              </div>
            </div>
            <div id="codeExamplesPanel" style="display: none;"></div>
          </div>
        </div>
      </div>
    </main>
  </div>

  <script>
    const LOGO_SVG = '<svg viewBox="0 0 614 614" fill="currentColor" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M110.72 110.72V186.212L186.217 186.212L186.217 110.72Z"/><path d="M110.72 427.786V503.277L186.217 503.277L186.217 427.786Z"/><path d="M427.783 110.72V186.212L503.28 186.212L503.28 110.72Z"/><path d="M427.779 427.786V503.277L503.275 503.277L503.275 427.786Z"/><path d="M306.998 447.914L362.358 503.277L306.998 558.64L251.637 503.277Z"/><path d="M306.998 55.3602L362.358 110.723L306.998 166.085L251.637 110.723Z"/><path d="M306.998 251.642L362.358 307.005L306.998 362.367L251.637 307.005Z"/><path d="M503.275 251.637L558.635 307L503.275 362.363L447.914 307Z"/><path d="M110.72 251.637L166.081 307L110.72 362.363L55.3602 307Z"/></svg>';

    // Global state
    let apiDocs = null;
    let currentModule = null;
    let currentEndpoint = null;
    let currentTab = 'tryit';

    // Default configuration - can be changed by user
    const config = {
      baseUrl: localStorage.getItem('pipeshub_api_base_url') || window.location.origin,
      authToken: localStorage.getItem('pipeshub_api_auth_token') || ''
    };

    // Initialize
    document.addEventListener('DOMContentLoaded', async () => {
      initializeTheme();
      await loadApiDocs();
      setupEventListeners();
      loadSavedConfig();
      initializeResizeHandles();
      showWelcomeScreen();
    });

    // Initialize resizable panels
    function initializeResizeHandles() {
      const sidebar = document.querySelector('.sidebar');
      const sidebarResizeHandle = document.getElementById('sidebarResizeHandle');
      const codePanel = document.getElementById('codePanel');
      const codePanelResizeHandle = document.getElementById('codePanelResizeHandle');

      // Sidebar resize
      setupResize(sidebarResizeHandle, sidebar, {
        minWidth: 200,
        maxWidth: 500,
        direction: 'right',
        storageKey: 'pipeshub_docs_sidebar_width'
      });

      // Code panel resize
      setupResize(codePanelResizeHandle, codePanel, {
        minWidth: 300,
        maxWidth: 800,
        direction: 'left',
        storageKey: 'pipeshub_docs_codepanel_width'
      });

      // Restore saved widths
      const savedSidebarWidth = localStorage.getItem('pipeshub_docs_sidebar_width');
      if (savedSidebarWidth) {
        sidebar.style.width = savedSidebarWidth + 'px';
      }

      const savedCodePanelWidth = localStorage.getItem('pipeshub_docs_codepanel_width');
      if (savedCodePanelWidth) {
        codePanel.style.width = savedCodePanelWidth + 'px';
      }
    }

    function setupResize(handle, panel, options) {
      if (!handle || !panel) return;

      let isResizing = false;
      let startX = 0;
      let startWidth = 0;

      handle.addEventListener('mousedown', (e) => {
        isResizing = true;
        startX = e.clientX;
        startWidth = panel.offsetWidth;

        handle.classList.add('dragging');
        document.body.classList.add('resizing');

        e.preventDefault();
      });

      document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;

        let newWidth;
        if (options.direction === 'right') {
          // Sidebar: dragging right increases width
          newWidth = startWidth + (e.clientX - startX);
        } else {
          // Code panel: dragging left increases width
          newWidth = startWidth - (e.clientX - startX);
        }

        // Apply constraints
        newWidth = Math.max(options.minWidth, Math.min(options.maxWidth, newWidth));

        panel.style.width = newWidth + 'px';
      });

      document.addEventListener('mouseup', () => {
        if (isResizing) {
          isResizing = false;
          handle.classList.remove('dragging');
          document.body.classList.remove('resizing');

          // Save width to localStorage
          if (options.storageKey) {
            localStorage.setItem(options.storageKey, panel.offsetWidth);
          }
        }
      });
    }

    // Initialize theme from localStorage or system preference
    function initializeTheme() {
      const savedTheme = localStorage.getItem('pipeshub_docs_theme');
      if (savedTheme) {
        document.documentElement.setAttribute('data-theme', savedTheme);
      } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        document.documentElement.setAttribute('data-theme', 'dark');
      }
    }

    // Toggle theme
    function toggleTheme() {
      const currentTheme = document.documentElement.getAttribute('data-theme');
      const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

      if (newTheme === 'light') {
        document.documentElement.removeAttribute('data-theme');
      } else {
        document.documentElement.setAttribute('data-theme', 'dark');
      }

      localStorage.setItem('pipeshub_docs_theme', newTheme);
    }

    // Load saved configuration
    function loadSavedConfig() {
      document.getElementById('baseUrlInput').value = config.baseUrl;
      document.getElementById('authTokenInput').value = config.authToken;
    }

    // Save configuration to localStorage
    function saveConfig() {
      config.baseUrl = document.getElementById('baseUrlInput').value;
      config.authToken = document.getElementById('authTokenInput').value;
      localStorage.setItem('pipeshub_api_base_url', config.baseUrl);
      localStorage.setItem('pipeshub_api_auth_token', config.authToken);
    }

    // Load API documentation
    async function loadApiDocs() {
      try {
        const response = await fetch('/api/v1/docs/json');
        apiDocs = await response.json();
        if (apiDocs.info?.version) document.getElementById('apiVersion').textContent = 'v' + apiDocs.info.version;
        renderSidebar();
      } catch (error) {
        console.error('Failed to load API docs:', error);
        document.getElementById('sidebarNav').innerHTML =
          '<div class="sidebar-empty" style="color: var(--red-11);">Failed to load documentation</div>';
      }
    }

    // Resolve $ref references in schema
    function resolveRef(ref) {
      if (!ref || !ref.startsWith('#/components/schemas/')) return null;
      const schemaName = ref.replace('#/components/schemas/', '');
      return apiDocs.schemas[schemaName] || null;
    }

    // Resolve schema recursively
    function resolveSchema(schema, depth = 0) {
      if (!schema || depth > 5) return schema; // Prevent infinite recursion

      if (schema.$ref) {
        const resolved = resolveRef(schema.$ref);
        if (resolved) {
          return resolveSchema(resolved, depth + 1);
        }
        return schema;
      }

      if (schema.type === 'array' && schema.items) {
        return {
          ...schema,
          items: resolveSchema(schema.items, depth + 1)
        };
      }

      if (schema.type === 'object' && schema.properties) {
        const resolvedProps = {};
        for (const [key, value] of Object.entries(schema.properties)) {
          resolvedProps[key] = resolveSchema(value, depth + 1);
        }
        return {
          ...schema,
          properties: resolvedProps
        };
      }

      if (schema.allOf) {
        let merged = {};
        for (const item of schema.allOf) {
          const resolved = resolveSchema(item, depth + 1);
          merged = { ...merged, ...resolved };
          if (resolved.properties) {
            merged.properties = { ...merged.properties, ...resolved.properties };
          }
        }
        return merged;
      }

      if (schema.oneOf || schema.anyOf) {
        const key = schema.oneOf ? 'oneOf' : 'anyOf';
        return {
          ...schema,
          [key]: schema[key].map(s => resolveSchema(s, depth + 1))
        };
      }

      return schema;
    }

    // Render schema as HTML
    function renderSchema(schema, name = null) {
      const resolved = resolveSchema(schema);
      if (!resolved) {
        return '<div class="code-block"><pre>' + escapeHtml(JSON.stringify(schema, null, 2)) + '</pre></div>';
      }

      let html = '<div class="schema-block">';

      // Schema header
      const schemaName = name || (schema.$ref ? schema.$ref.replace('#/components/schemas/', '') : 'Schema');
      html += '<div class="schema-header">';
      html += icon('chevron_right');
      html += '<span class="schema-name">' + escapeHtml(schemaName) + '</span>';
      html += '<span class="schema-toggle">' + schemaSummary(resolved) + '</span>';
      html += '</div>';

      html += '<div class="schema-content">';

      // Render as detailed table
      html += renderSchemaTable(resolved, [], 0);

      html += '</div></div>';
      return html;
    }

    function schemaSummary(schema) {
      if (schema.properties) {
        const n = Object.keys(schema.properties).length;
        return n + (n === 1 ? ' field' : ' fields');
      }
      return escapeHtml(getSchemaType(schema));
    }

    // Render schema as detailed table
    function renderSchemaTable(schema, requiredFields, depth) {
      if (depth > 6) return '<div class="schema-scalar">...</div>';

      const resolved = resolveSchema(schema);
      if (!resolved) return '';

      let html = '<table class="schema-table">';
      html += '<thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>';
      html += '<tbody>';

      if (resolved.properties) {
        const required = requiredFields.length > 0 ? requiredFields : (resolved.required || []);
        for (const [propName, propValue] of Object.entries(resolved.properties)) {
          html += renderSchemaRow(propName, propValue, required.includes(propName), depth);
        }
      } else if (resolved.type === 'array' && resolved.items) {
        html += '<tr><td colspan="3">';
        html += '<div class="schema-oneof-caption">Array of <span class="schema-field-type">' + escapeHtml(getSchemaType(resolved.items)) + '</span></div>';
        html += renderSchemaTable(resolved.items, [], depth + 1);
        html += '</td></tr>';
      } else {
        html += '<tr><td colspan="3">';
        html += '<span class="schema-field-type">' + escapeHtml(getSchemaType(resolved)) + '</span>';
        if (resolved.description) {
          html += '<div class="md small" style="margin-top: 6px;">' + renderMarkdown(resolved.description) + '</div>';
        }
        html += '</td></tr>';
      }

      html += '</tbody></table>';
      return html;
    }

    // Render a single schema row
    function renderSchemaRow(propName, propSchema, isRequired, depth) {
      const resolved = resolveSchema(propSchema);
      if (!resolved) return '';

      let html = '<tr>';

      // Field name column
      html += '<td>';
      html += '<span class="schema-field-name">' + escapeHtml(propName) + '</span>';
      if (isRequired) {
        html += '<span class="schema-field-required">required</span>';
      }
      html += '</td>';

      // Type column
      html += '<td>';
      html += '<span class="schema-field-type">' + escapeHtml(getSchemaType(propSchema)) + '</span>';
      if (resolved.format) {
        html += '<span class="schema-format-badge">' + escapeHtml(resolved.format) + '</span>';
      }
      html += '</td>';

      // Description column
      html += '<td>';
      html += '<div class="schema-field-desc">';

      // Description text
      if (resolved.description) {
        html += '<div class="md small">' + renderMarkdown(resolved.description) + '</div>';
      }

      // Enum values
      const enumValues = resolved.enum || (resolved.type === 'array' && resolveSchema(resolved.items)?.enum);
      if (enumValues) {
        html += '<div class="schema-field-enum">One of';
        for (const val of enumValues) {
          html += '<span class="schema-field-enum-value">' + escapeHtml(String(val)) + '</span>';
        }
        html += '</div>';
      }

      // Constraints
      const constraints = [];
      if (resolved.minLength !== undefined) constraints.push('minLength: ' + resolved.minLength);
      if (resolved.maxLength !== undefined) constraints.push('maxLength: ' + resolved.maxLength);
      if (resolved.minimum !== undefined) constraints.push('min: ' + resolved.minimum);
      if (resolved.maximum !== undefined) constraints.push('max: ' + resolved.maximum);
      if (resolved.pattern) constraints.push('pattern: ' + resolved.pattern);
      if (resolved.minItems !== undefined) constraints.push('minItems: ' + resolved.minItems);
      if (resolved.maxItems !== undefined) constraints.push('maxItems: ' + resolved.maxItems);

      if (resolved.default !== undefined) {
        constraints.push('default: ' + (typeof resolved.default === 'object' ? JSON.stringify(resolved.default) : String(resolved.default)));
      }
      if (resolved.nullable) constraints.push('nullable');
      if (resolved.readOnly) constraints.push('read-only');
      if (resolved.deprecated) constraints.push('deprecated');

      const example = resolved.example !== undefined
        ? (typeof resolved.example === 'object' ? JSON.stringify(resolved.example) : String(resolved.example))
        : null;
      if (constraints.length > 0 || example !== null) {
        html += '<div class="schema-meta">';
        if (example !== null) html += '<span class="schema-field-example">e.g. ' + escapeHtml(example) + '</span>';
        for (const c of constraints) {
          html += '<span>' + escapeHtml(c) + '</span>';
        }
        html += '</div>';
      }

      // Handle oneOf/anyOf
      if (resolved.oneOf || resolved.anyOf) {
        const variants = resolved.oneOf || resolved.anyOf;
        const variantType = resolved.oneOf ? 'oneOf' : 'anyOf';
        const variantId = 'variant_' + Math.random().toString(36).substr(2, 9);

        html += '<div class="schema-oneof-container">';
        html += '<div class="schema-oneof-caption">' + (variantType === 'oneOf' ? 'Exactly one of' : 'Any of') + '</div>';
        html += '<div class="schema-oneof-tabs">';

        variants.forEach((variant, idx) => {
          const variantResolved = resolveSchema(variant);
          const variantName = variant.$ref
            ? variant.$ref.replace('#/components/schemas/', '')
            : (variantResolved?.type || 'Option ' + (idx + 1));
          html += '<span class="schema-oneof-tab' + (idx === 0 ? ' active' : '') + '" ';
          html += 'data-variant-group="' + variantId + '" data-variant-idx="' + idx + '">';
          html += escapeHtml(variantName);
          html += '</span>';
        });

        html += '</div>';

        variants.forEach((variant, idx) => {
          html += '<div class="schema-oneof-content' + (idx === 0 ? ' active' : '') + '" ';
          html += 'data-variant-group="' + variantId + '" data-variant-idx="' + idx + '">';

          const variantResolved = resolveSchema(variant);
          if (variantResolved) {
            if (variantResolved.properties && depth < 4) {
              html += renderSchemaTable(variantResolved, variantResolved.required || [], depth + 1);
            } else {
              html += '<div class="schema-scalar"><span class="schema-field-type">' + escapeHtml(getSchemaType(variantResolved)) + '</span>';
              if (variantResolved.description) {
                html += '<div class="md small" style="margin-top: 6px;">' + renderMarkdown(variantResolved.description) + '</div>';
              }
              html += '</div>';
            }
          }

          html += '</div>';
        });

        html += '</div>';
      }

      // Nested object properties
      if (resolved.properties && !resolved.oneOf && !resolved.anyOf && depth < 4) {
        const nestedId = 'nested_' + Math.random().toString(36).substr(2, 9);
        html += '<div class="schema-nested">';
        html += '<div class="schema-nested-header" data-nested-id="' + nestedId + '">';
        html += icon('chevron_right', 'schema-nested-toggle', 16);
        html += '<span>' + Object.keys(resolved.properties).length + ' nested fields</span>';
        html += '</div>';
        html += '<div class="schema-nested-content" data-nested-id="' + nestedId + '">';
        html += renderSchemaTable(resolved, resolved.required || [], depth + 1);
        html += '</div>';
        html += '</div>';
      }

      // Array items
      if (resolved.type === 'array' && resolved.items && depth < 4) {
        const itemsResolved = resolveSchema(resolved.items);
        if (itemsResolved?.properties) {
          const nestedId = 'nested_' + Math.random().toString(36).substr(2, 9);
          html += '<div class="schema-nested">';
          html += '<div class="schema-nested-header" data-nested-id="' + nestedId + '">';
          html += icon('chevron_right', 'schema-nested-toggle', 16);
          html += '<span>Item fields</span>';
          html += '</div>';
          html += '<div class="schema-nested-content" data-nested-id="' + nestedId + '">';
          html += renderSchemaTable(resolved.items, itemsResolved.required || [], depth + 1);
          html += '</div>';
          html += '</div>';
        }
      }

      html += '</div>';
      html += '</td>';
      html += '</tr>';

      return html;
    }

    // Get schema type as string
    function getSchemaType(schema) {
      if (!schema) return 'any';

      if (schema.$ref) {
        return schema.$ref.replace('#/components/schemas/', '');
      }
      if (schema.oneOf) {
        const types = schema.oneOf.map(s => getSchemaType(s));
        return types.join(' | ');
      }
      if (schema.anyOf) {
        const types = schema.anyOf.map(s => getSchemaType(s));
        return types.join(' | ');
      }
      if (schema.type === 'array') {
        return 'array<' + getSchemaType(schema.items || {}) + '>';
      }
      if (schema.enum) {
        return 'enum';
      }
      return schema.type || 'object';
    }

    // Generate example from schema
    function generateExample(schema, depth = 0) {
      if (depth > 6) return '...';

      const resolved = resolveSchema(schema);
      if (!resolved) return {};

      // Check for explicit example or default first
      if (resolved.example !== undefined) return resolved.example;
      if (resolved.default !== undefined) return resolved.default;

      // Next to properties, a oneOf/anyOf only constrains them (e.g. "password only for demo emails"), it is not the shape.
      if (resolved.properties) {
        // Optional fields the spec gives no example for would only add placeholders the API may refuse.
        const required = resolved.required || [];
        const documented = (key, value) => {
          const v = resolveSchema(value) || {};
          return required.includes(key) || v.example !== undefined || v.default !== undefined || v.enum !== undefined;
        };
        const entries = Object.entries(resolved.properties);
        const keep = entries.some(([key, value]) => documented(key, value)) ? entries.filter(([key, value]) => documented(key, value)) : entries;
        const obj = {};
        for (const [key, value] of keep) {
          obj[key] = generateExample(value, depth + 1);
        }
        return obj;
      }

      // Handle oneOf/anyOf - pick the first option that produces a meaningful example
      if (resolved.oneOf && resolved.oneOf.length > 0) {
        // Try to find a non-string option first (usually more informative)
        for (const option of resolved.oneOf) {
          const optResolved = resolveSchema(option);
          if (optResolved && (optResolved.type === 'object' || optResolved.properties)) {
            return generateExample(option, depth + 1);
          }
        }
        // Fall back to first option
        return generateExample(resolved.oneOf[0], depth + 1);
      }

      if (resolved.anyOf && resolved.anyOf.length > 0) {
        // Try to find a non-string option first
        for (const option of resolved.anyOf) {
          const optResolved = resolveSchema(option);
          if (optResolved && (optResolved.type === 'object' || optResolved.properties)) {
            return generateExample(option, depth + 1);
          }
        }
        return generateExample(resolved.anyOf[0], depth + 1);
      }

      // Handle allOf - merge all schemas
      if (resolved.allOf && resolved.allOf.length > 0) {
        let mergedExample = {};
        for (const item of resolved.allOf) {
          const itemExample = generateExample(item, depth + 1);
          if (typeof itemExample === 'object' && itemExample !== null) {
            mergedExample = { ...mergedExample, ...itemExample };
          }
        }
        return mergedExample;
      }

      switch (resolved.type) {
        case 'string':
          if (resolved.enum && resolved.enum.length > 0) return resolved.enum[0];
          if (resolved.format === 'date-time') return new Date().toISOString();
          if (resolved.format === 'date') return new Date().toISOString().split('T')[0];
          if (resolved.format === 'email') return 'user@example.com';
          if (resolved.format === 'uri' || resolved.format === 'url') return 'https://example.com';
          if (resolved.format === 'uuid') return '550e8400-e29b-41d4-a716-446655440000';
          if (resolved.format === 'password') return 'password123';
          if (resolved.format === 'binary') return '<binary data>';
          return 'string';
        case 'number':
          if (resolved.example !== undefined) return resolved.example;
          if (resolved.minimum !== undefined) return resolved.minimum;
          if (resolved.maximum !== undefined) return Math.floor(resolved.maximum / 2);
          return 0;
        case 'integer':
          if (resolved.example !== undefined) return resolved.example;
          if (resolved.minimum !== undefined) return resolved.minimum;
          if (resolved.maximum !== undefined) return Math.floor(resolved.maximum / 2);
          return 0;
        case 'boolean':
          return true;
        case 'array':
          if (resolved.items) {
            return [generateExample(resolved.items, depth + 1)];
          }
          return [];
        case 'object':
          if (resolved.properties) {
            const obj = {};
            for (const [key, value] of Object.entries(resolved.properties)) {
              obj[key] = generateExample(value, depth + 1);
            }
            return obj;
          }
          // Object without properties - return empty object
          if (resolved.additionalProperties) {
            return { key: generateExample(resolved.additionalProperties, depth + 1) };
          }
          return {};
        default:
          // No explicit type but has properties - treat as object
          if (resolved.properties) {
            const obj = {};
            for (const [key, value] of Object.entries(resolved.properties)) {
              obj[key] = generateExample(value, depth + 1);
            }
            return obj;
          }
          // Check for enum without type
          if (resolved.enum && resolved.enum.length > 0) {
            return resolved.enum[0];
          }
          // Check for additionalProperties
          if (resolved.additionalProperties) {
            return { key: generateExample(resolved.additionalProperties, depth + 1) };
          }
          // Return empty object instead of null for unknown types
          return {};
      }
    }

    // Render sidebar navigation
    function renderSidebar() {
      const nav = document.getElementById('sidebarNav');
      let html = '';

      for (const category of apiDocs.categories) {
        if (category.modules.length === 0) continue;

        html += '<div class="nav-category">';
        html += '<div class="category-header">' + escapeHtml(category.name) + '</div>';

        for (const module of category.modules) {
          html += renderModuleNavItem(module);
        }

        html += '</div>';
      }

      html += '<div class="sidebar-empty" id="searchEmpty" style="display: none;">No endpoints match your search.</div>';
      nav.innerHTML = html;
    }

    // Render a single module nav item with its endpoints
    function renderModuleNavItem(module) {
      let html = '';
      html += '<a class="nav-item" data-module="' + escapeHtml(module.id) + '">';
      html += '<span class="nav-item-icon">' + icon(moduleIconName(module.icon)) + '</span>';
      html += '<span>' + escapeHtml(module.name) + '</span>';
      html += icon('chevron_right', 'nav-item-chevron', 16);
      html += '</a>';

      html += '<div class="endpoint-list" data-module="' + escapeHtml(module.id) + '" style="display: none;">';
      for (const tag of module.tags) {
        const endpoints = tagEndpoints(tag);
        html += '<div class="tag-section" data-module="' + escapeHtml(module.id) + '" data-tag="' + escapeHtml(tag) + '">';
        html += '<div class="tag-header">';
        html += icon('chevron_right', 'tag-chevron', 16);
        html += '<span class="tag-name">' + escapeHtml(tag) + '</span>';
        html += '<span class="tag-count">' + endpoints.length + '</span>';
        html += '</div>';

        html += '<div class="tag-endpoints">';
        for (const endpoint of endpoints) {
          html += '<div class="endpoint-item" data-endpoint="' + escapeHtml(endpoint.operationId) + '">';
          html += methodBadge(endpoint.method);
          html += '<span>' + escapeHtml(endpoint.summary || endpoint.path) + '</span>';
          html += '</div>';
        }
        html += '</div>';
        html += '</div>';
      }
      html += '</div>';

      return html;
    }

    // An endpoint with several tags is listed under each of them.
    function tagEndpoints(tag) {
      return apiDocs.endpoints.filter(e => e.tags.includes(tag));
    }

    function icon(name, className, size) {
      return '<span class="material-icons-outlined' + (className ? ' ' + className : '') + '" aria-hidden="true"' +
        (size ? ' style="font-size: ' + size + 'px;"' : '') + '>' + name + '</span>';
    }

    function methodBadge(method, className) {
      const m = String(method).toLowerCase();
      return '<span class="method-badge method-' + escapeHtml(m) + (className ? ' ' + className : '') + '">' + escapeHtml(String(method).toUpperCase()) + '</span>';
    }

    // The spec's x-pipeshub-nav names icons in its own vocabulary; map them to Material Icons, as the dashboard uses.
    function moduleIconName(name) {
      const icons = {
        lock: 'lock', user: 'group', folder: 'folder', book: 'menu_book', search: 'search', code: 'hub',
        gear: 'settings', clock: 'schedule', mail: 'mail', layers: 'layers', key: 'key', image: 'image',
        cpu: 'smart_toy', tool: 'build', server: 'dns', activity: 'monitor_heart',
      };
      return icons[name] || 'description';
    }

    // Setup event listeners
    function setupEventListeners() {
      // Module click
      document.getElementById('sidebarNav').addEventListener('click', (e) => {
        const navItem = e.target.closest('.nav-item');
        if (navItem) {
          const moduleId = navItem.dataset.module;
          selectModule(moduleId);
        }

        const endpointItem = e.target.closest('.endpoint-item');
        if (endpointItem && endpointItem.dataset.endpoint) {
          const operationId = endpointItem.dataset.endpoint;
          selectEndpoint(operationId, endpointItem.closest('.tag-section')?.dataset.tag);
          closeSidebar();
        }

        // Tag header click to expand/collapse
        const tagHeader = e.target.closest('.tag-header');
        if (tagHeader) {
          const tagSection = tagHeader.closest('.tag-section');
          if (tagSection) {
            tagSection.classList.toggle('expanded');
          }
        }
      });

      // Breadcrumb click
      document.getElementById('breadcrumb').addEventListener('click', (e) => {
        const link = e.target.closest('.breadcrumb-link');
        if (link) {
          const action = link.dataset.action;
          if (action === 'home') {
            showWelcomeScreen();
          } else if (action === 'module' && link.dataset.moduleId) {
            selectModule(link.dataset.moduleId);
          } else if (action === 'tag' && link.dataset.moduleId && link.dataset.tag) {
            showTagEndpoints(link.dataset.moduleId, link.dataset.tag);
          }
        }
      });

      // Module card click (for tag navigation)
      document.getElementById('docPanel').addEventListener('click', (e) => {
        const moduleCard = e.target.closest('.module-card');
        if (moduleCard && moduleCard.dataset.moduleId && moduleCard.dataset.tag) {
          showTagEndpoints(moduleCard.dataset.moduleId, moduleCard.dataset.tag);
        }

        const endpointRow = e.target.closest('.endpoint-row');
        if (endpointRow && endpointRow.dataset.endpoint) {
          selectEndpoint(endpointRow.dataset.endpoint, endpointRow.dataset.tag);
        }

        const copyPath = e.target.closest('[data-copy]');
        if (copyPath) {
          copyText(copyPath.dataset.copy, copyPath);
        }

        // Schema block toggle
        const schemaHeader = e.target.closest('.schema-header');
        if (schemaHeader) {
          schemaHeader.parentElement.classList.toggle('expanded');
        }

        // Nested schema toggle
        const nestedHeader = e.target.closest('.schema-nested-header');
        if (nestedHeader) {
          const nestedId = nestedHeader.dataset.nestedId;
          const toggle = nestedHeader.querySelector('.schema-nested-toggle');
          const content = document.querySelector('.schema-nested-content[data-nested-id="' + nestedId + '"]');
          if (toggle && content) {
            toggle.classList.toggle('expanded');
            content.classList.toggle('expanded');
          }
        }

        // OneOf/AnyOf tabs
        const variantTab = e.target.closest('.schema-oneof-tab');
        if (variantTab) {
          const groupId = variantTab.dataset.variantGroup;
          const idx = variantTab.dataset.variantIdx;

          // Update tabs
          document.querySelectorAll('.schema-oneof-tab[data-variant-group="' + groupId + '"]').forEach(tab => {
            tab.classList.remove('active');
          });
          variantTab.classList.add('active');

          // Update content
          document.querySelectorAll('.schema-oneof-content[data-variant-group="' + groupId + '"]').forEach(content => {
            content.classList.remove('active');
          });
          const activeContent = document.querySelector('.schema-oneof-content[data-variant-group="' + groupId + '"][data-variant-idx="' + idx + '"]');
          if (activeContent) {
            activeContent.classList.add('active');
          }
        }
      });

      // Search
      document.getElementById('searchInput').addEventListener('input', (e) => {
        filterEndpoints(e.target.value);
      });

      // Code panel tabs
      document.querySelector('.code-panel-header')?.addEventListener('click', (e) => {
        const tab = e.target.closest('.code-tab');
        if (tab && tab.dataset.tab) {
          document.querySelectorAll('.code-tab').forEach(t => t.classList.remove('active'));
          tab.classList.add('active');
          currentTab = tab.dataset.tab;
          switchTab(tab.dataset.tab);
        }
      });

      // Save config on input change
      document.getElementById('baseUrlInput')?.addEventListener('change', saveConfig);
      document.getElementById('authTokenInput')?.addEventListener('change', saveConfig);

      // Execute button
      document.getElementById('executeBtn')?.addEventListener('click', executeRequest);

      // Input mode toggle (Form/Raw JSON)
      document.getElementById('inputModeToggle')?.addEventListener('click', (e) => {
        const btn = e.target.closest('.input-mode-btn');
        if (btn && btn.dataset.mode) {
          toggleInputMode(btn.dataset.mode);
        }
      });

      // Validate raw JSON on input
      document.getElementById('rawJsonTextarea')?.addEventListener('input', validateRawJson);

      // Response headers toggle - use event delegation on the section
      document.getElementById('responseHeadersSection')?.addEventListener('click', (e) => {
        if (e.target.closest('.response-headers-toggle')) {
          toggleResponseHeaders();
        }
      });

      // Theme toggle
      document.getElementById('themeToggle')?.addEventListener('click', toggleTheme);

      // Sidebar drawer on narrow screens
      document.getElementById('menuToggle').addEventListener('click', () => {
        document.getElementById('sidebar').classList.add('open');
        document.getElementById('sidebarBackdrop').classList.add('open');
      });
      document.getElementById('sidebarBackdrop').addEventListener('click', closeSidebar);
    }

    function closeSidebar() {
      document.getElementById('sidebar').classList.remove('open');
      document.getElementById('sidebarBackdrop').classList.remove('open');
    }

    function copyText(text, button) {
      navigator.clipboard?.writeText(text);
      const label = button.querySelector('.copy-label');
      if (!label) return;
      label.textContent = 'Copied';
      setTimeout(() => { label.textContent = 'Copy'; }, 1500);
    }

    function copyButton(text) {
      return '<button type="button" class="copy-btn" data-copy="' + escapeHtml(text) + '">' + icon('content_copy') + '<span class="copy-label">Copy</span></button>';
    }

    // Switch between tabs
    function switchTab(tab) {
      const tryItPanel = document.getElementById('tryItPanel');
      const codeExamplesPanel = document.getElementById('codeExamplesPanel');

      if (tab === 'tryit') {
        tryItPanel.style.display = 'block';
        codeExamplesPanel.style.display = 'none';
      } else {
        tryItPanel.style.display = 'none';
        codeExamplesPanel.style.display = 'block';
        updateCodeExample(tab);
      }
    }

    // Track current input mode (form or raw)
    let currentInputMode = 'form';

    // Toggle between Form and Raw JSON input modes
    function toggleInputMode(mode) {
      currentInputMode = mode;

      const formInputs = document.getElementById('requestBodyInputs');
      const rawJsonInput = document.getElementById('rawJsonInput');
      const rawJsonTextarea = document.getElementById('rawJsonTextarea');

      // Update button states
      document.querySelectorAll('.input-mode-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === mode);
      });

      if (mode === 'form') {
        formInputs.style.display = 'block';
        rawJsonInput.style.display = 'none';
      } else {
        formInputs.style.display = 'none';
        rawJsonInput.style.display = 'block';

        // Pre-populate raw JSON from form fields if empty
        if (!rawJsonTextarea.value.trim()) {
          const bodyFromFields = collectBodyFields();
          if (Object.keys(bodyFromFields).length > 0) {
            rawJsonTextarea.value = JSON.stringify(bodyFromFields, null, 2);
          } else if (currentEndpoint?.requestBody?.content?.['application/json']?.schema) {
            // Generate example from schema
            const example = generateExample(currentEndpoint.requestBody.content['application/json'].schema);
            rawJsonTextarea.value = JSON.stringify(example, null, 2);
          }
        }
        validateRawJson();
      }
    }

    // Validate raw JSON input
    function validateRawJson() {
      const textarea = document.getElementById('rawJsonTextarea');
      const errorDiv = document.getElementById('rawJsonError');

      if (!textarea.value.trim()) {
        textarea.classList.remove('error');
        errorDiv.style.display = 'none';
        return true;
      }

      try {
        JSON.parse(textarea.value);
        textarea.classList.remove('error');
        errorDiv.style.display = 'none';
        return true;
      } catch (e) {
        textarea.classList.add('error');
        errorDiv.textContent = 'Invalid JSON: ' + e.message;
        errorDiv.style.display = 'block';
        return false;
      }
    }

    // Get request body based on current input mode
    function getRequestBody() {
      if (currentInputMode === 'raw') {
        const rawJson = document.getElementById('rawJsonTextarea')?.value?.trim();
        if (rawJson) {
          try {
            return JSON.parse(rawJson);
          } catch (e) {
            return null;
          }
        }
        return {};
      }
      return collectBodyFields();
    }

    // Execute API request
    async function executeRequest() {
      if (!currentEndpoint) return;

      const ep = currentEndpoint;
      const configuredBaseUrl = document.getElementById('baseUrlInput').value || config.baseUrl;
      const authToken = document.getElementById('authTokenInput').value || config.authToken;

      // Build URL with path params - check if basePath is already a full URL
      const isFullUrl = ep.basePath.startsWith('http://') || ep.basePath.startsWith('https://');
      let url = isFullUrl ? ep.basePath + ep.path : configuredBaseUrl + ep.basePath + ep.path;

      // Replace path parameters
      const pathParams = ep.parameters?.filter(p => p.in === 'path') || [];
      for (const param of pathParams) {
        const input = document.querySelector('[data-param="' + param.name + '"]');
        if (input) {
          url = url.replace('{' + param.name + '}', encodeURIComponent(input.value));
        }
      }

      // Add query parameters
      const queryParams = ep.parameters?.filter(p => p.in === 'query') || [];
      const queryString = queryParams
        .map(param => {
          const input = document.querySelector('[data-param="' + param.name + '"]');
          if (input && input.value) {
            return encodeURIComponent(param.name) + '=' + encodeURIComponent(input.value);
          }
          return null;
        })
        .filter(Boolean)
        .join('&');

      if (queryString) {
        url += '?' + queryString;
      }

      // Build request options
      const options = {
        method: ep.method,
        headers: {}
      };

      // Add authorization header
      if (authToken) {
        options.headers['Authorization'] = authToken.startsWith('Bearer ') ? authToken : 'Bearer ' + authToken;
      }

      // Add custom headers
      const headerInputs = document.querySelectorAll('[data-header]');
      headerInputs.forEach(input => {
        if (input.value) {
          options.headers[input.dataset.header] = input.value;
        }
      });

      // Check if there are file uploads
      const fileInputs = document.querySelectorAll('[data-file-field]');
      const hasFiles = Array.from(fileInputs).some(input => input.files && input.files.length > 0);

      // Build request body from fields
      if (['POST', 'PUT', 'PATCH'].includes(ep.method)) {
        if (hasFiles) {
          // Use FormData for file uploads
          const formData = new FormData();

          // Add file fields
          fileInputs.forEach(input => {
            if (input.files && input.files[0]) {
              formData.append(input.dataset.fileField, input.files[0]);
            }
          });

          // Add other body fields
          const bodyFields = collectBodyFields();
          for (const [key, value] of Object.entries(bodyFields)) {
            if (typeof value === 'object') {
              formData.append(key, JSON.stringify(value));
            } else {
              formData.append(key, String(value));
            }
          }

          options.body = formData;
          // Don't set Content-Type header - browser will set it with boundary
        } else {
          // Use JSON for regular requests
          // Check if using raw JSON mode and validate
          if (currentInputMode === 'raw' && !validateRawJson()) {
            document.getElementById('rawJsonTextarea').focus();
            return;
          }
          const body = getRequestBody();
          if (body && Object.keys(body).length > 0) {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(body);
          }
        }
      }

      // Show loading state
      const executeBtn = document.getElementById('executeBtn');
      const originalText = executeBtn.innerHTML;
      executeBtn.innerHTML = '<span class="spinner" style="width: 14px; height: 14px; border-color: rgba(255,255,255,0.4); border-top-color: #fff;"></span>Sending...';
      executeBtn.disabled = true;
      const startedAt = performance.now();
      const responseTime = document.getElementById('responseTime');

      const responseSection = document.getElementById('responseSection');
      const responseStatus = document.getElementById('responseStatus');
      const responseBody = document.getElementById('responseBody');

      try {
        const response = await fetch(url, options);
        const contentType = response.headers.get('content-type');
        let data;

        if (contentType && contentType.includes('application/json')) {
          data = await response.json();
        } else {
          data = await response.text();
        }

        responseSection.style.display = 'block';
        responseStatus.textContent = response.status + ' ' + response.statusText;
        responseStatus.className = 'response-status ' + (response.ok ? 'success' : 'error');
        responseBody.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
        responseTime.textContent = Math.round(performance.now() - startedAt) + ' ms';

        // Display response headers
        displayResponseHeaders(response.headers);
      } catch (error) {
        responseSection.style.display = 'block';
        responseStatus.textContent = 'Network error';
        responseStatus.className = 'response-status error';
        responseTime.textContent = '';
        // fetch only says "Failed to fetch"; the usual causes are a wrong base URL or CORS on another origin
        responseBody.textContent = error.message + '\\n\\nCheck the base URL. A host other than this page must allow it through CORS.';
        // Hide headers section on error
        const headersSection = document.getElementById('responseHeadersSection');
        if (headersSection) headersSection.style.display = 'none';
      } finally {
        executeBtn.innerHTML = originalText;
        executeBtn.disabled = false;
        responseSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }

    // Display response headers in collapsible section
    function displayResponseHeaders(headers) {
      const headersSection = document.getElementById('responseHeadersSection');
      const headersCount = document.getElementById('responseHeadersCount');
      const headersContent = document.getElementById('responseHeadersContent');

      if (!headersSection || !headersCount || !headersContent) return;

      // Convert headers to array using entries() iterator
      const headerEntries = [];
      try {
        // Use entries() which is more widely supported
        for (const [name, value] of headers.entries()) {
          headerEntries.push({ name, value });
        }
      } catch (e) {
        // Fallback: try forEach if entries() fails
        try {
          headers.forEach((value, name) => {
            headerEntries.push({ name, value });
          });
        } catch (e2) {
          console.warn('Could not read response headers:', e2);
        }
      }

      // Update count
      headersCount.textContent = headerEntries.length;

      // Build headers HTML
      if (headerEntries.length > 0) {
        headersSection.style.display = 'block';
        headersContent.innerHTML = headerEntries.map(h =>
          '<div class="header-row">' +
            '<span class="header-name">' + escapeHtml(h.name) + '</span>' +
            '<span class="header-value">' + escapeHtml(h.value) + '</span>' +
          '</div>'
        ).join('');
        // Ensure collapsed by default
        headersSection.classList.remove('expanded');
      } else {
        headersSection.style.display = 'none';
      }
    }

    // Toggle response headers section
    function toggleResponseHeaders() {
      const headersSection = document.getElementById('responseHeadersSection');
      if (headersSection) {
        headersSection.classList.toggle('expanded');
      }
    }

    // Collect body fields into object
    function collectBodyFields() {
      const body = {};

      // Collect regular fields
      const fieldInputs = document.querySelectorAll('[data-body-field]');
      fieldInputs.forEach(input => {
        const path = input.dataset.bodyField;
        let value = input.value;

        // Skip empty values
        if (value === '' || value === undefined) return;

        // Convert value types
        if (input.tagName === 'SELECT') {
          if (value === 'true') value = true;
          else if (value === 'false') value = false;
          else if (value === '') return; // Skip unselected
        } else if (input.type === 'number') {
          value = value === '' ? undefined : Number(value);
          if (value === undefined || isNaN(value)) return;
        }

        setNestedValue(body, path, value);
      });

      // Collect array fields
      const arrayContainers = document.querySelectorAll('[data-array-field]');
      arrayContainers.forEach(container => {
        const path = container.dataset.arrayField;
        const items = container.querySelectorAll('[data-array-item]');
        const values = [];

        items.forEach(item => {
          if (item.value) {
            // Try to parse as JSON for complex types
            try {
              values.push(JSON.parse(item.value));
            } catch {
              values.push(item.value);
            }
          }
        });

        if (values.length > 0) {
          setNestedValue(body, path, values);
        }
      });

      return body;
    }

    // Set nested value in object
    function setNestedValue(obj, path, value) {
      const parts = path.split('.');
      let current = obj;

      for (let i = 0; i < parts.length - 1; i++) {
        if (!current[parts[i]]) {
          current[parts[i]] = {};
        }
        current = current[parts[i]];
      }

      current[parts[parts.length - 1]] = value;
    }

    // Setup Try It panel for current endpoint
    function setupTryItPanel() {
      if (!currentEndpoint) return;

      const ep = currentEndpoint;

      // An operation whose server is an absolute URL is called there, not on this instance
      const baseUrlInput = document.getElementById('baseUrlInput');
      const isFullUrl = ep.basePath.startsWith('http://') || ep.basePath.startsWith('https://');
      baseUrlInput.value = isFullUrl ? ep.basePath : config.baseUrl;
      baseUrlInput.disabled = isFullUrl;

      for (const [location, sectionId, inputsId, dataAttr] of [
        ['header', 'headersSection', 'headersInputs', 'data-header'],
        ['path', 'pathParamsSection', 'pathParamsInputs', 'data-param'],
        ['query', 'queryParamsSection', 'queryParamsInputs', 'data-param'],
      ]) {
        const params = ep.parameters?.filter(p => p.in === location) || [];
        document.getElementById(sectionId).style.display = params.length > 0 ? 'block' : 'none';
        document.getElementById(inputsId).innerHTML = params.map(param => renderParamInput(param, dataAttr)).join('');
      }

      // Request body
      const requestBodySection = document.getElementById('requestBodySection');
      const requestBodyInputs = document.getElementById('requestBodyInputs');
      const inputModeToggle = document.getElementById('inputModeToggle');
      const rawJsonInput = document.getElementById('rawJsonInput');
      const rawJsonTextarea = document.getElementById('rawJsonTextarea');
      const requestBodyTab = document.getElementById('requestBodyTab');

      // Reset input mode to form
      currentInputMode = 'form';
      document.querySelectorAll('.input-mode-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.mode === 'form');
      });
      requestBodyInputs.style.display = 'block';
      rawJsonInput.style.display = 'none';
      rawJsonTextarea.value = '';
      document.getElementById('rawJsonError').style.display = 'none';

      if (ep.requestBody && ['POST', 'PUT', 'PATCH'].includes(ep.method)) {
        requestBodySection.style.display = 'block';

        // Check for multipart/form-data (file upload)
        const multipartContent = ep.requestBody.content?.['multipart/form-data'];
        const jsonContent = ep.requestBody.content?.['application/json'];

        if (multipartContent?.schema) {
          // Handle file upload - hide input mode toggle for file uploads
          inputModeToggle.style.display = 'none';
          requestBodyTab.style.display = 'none';
          const resolved = resolveSchema(multipartContent.schema);
          requestBodyInputs.innerHTML = renderMultipartFields(resolved, multipartContent.schema.required || resolved?.required || []);
        } else if (jsonContent?.schema) {
          // Show input mode toggle and Request Body tab for JSON content
          inputModeToggle.style.display = 'flex';
          requestBodyTab.style.display = 'flex';
          const resolved = resolveSchema(jsonContent.schema);
          requestBodyInputs.innerHTML = renderBodyFields(resolved, jsonContent.schema.required || resolved?.required || []);
        } else {
          inputModeToggle.style.display = 'none';
          requestBodyTab.style.display = 'none';
          requestBodyInputs.innerHTML = '<div class="body-field-desc">No schema available</div>';
        }
      } else {
        requestBodySection.style.display = 'none';
        requestBodyTab.style.display = 'none';
      }

      // Reset response
      document.getElementById('responseSection').style.display = 'none';
    }

    function renderParamInput(param, dataAttr) {
      const schema = resolveSchema(param.schema) || {};
      const attr = dataAttr + '="' + escapeHtml(param.name) + '"';
      const example = param.example !== undefined ? param.example : (schema.example !== undefined ? schema.example : schema.default);
      let html = '<div class="param-input-group">';
      html += '<label class="param-input-label">' + escapeHtml(param.name) +
        (param.required ? '<span class="required">*</span>' : '') +
        '<span class="field-type">' + escapeHtml(getSchemaType(schema)) + '</span></label>';
      const options = schema.enum || (schema.type === 'boolean' ? [true, false] : null);
      if (options) {
        html += '<select class="try-it-input" ' + attr + '><option value="">' + (param.required ? 'Select a value' : 'Not set') + '</option>';
        for (const opt of options) {
          html += '<option value="' + escapeHtml(String(opt)) + '">' + escapeHtml(String(opt)) + '</option>';
        }
        html += '</select>';
      } else {
        html += '<input type="text" class="try-it-input" ' + attr +
          ' placeholder="' + escapeHtml(example !== undefined ? String(example) : getSchemaType(schema)) + '">';
      }
      if (param.description) {
        html += '<div class="body-field-desc md">' + renderMarkdown(param.description) + '</div>';
      }
      return html + '</div>';
    }

    // Render body fields from schema
    function renderBodyFields(schema, requiredFields, prefix = '', depth = 0) {
      if (!schema || depth > 4) return '';

      const resolved = resolveSchema(schema);
      if (!resolved) return '';

      let html = '<div class="body-field-group">';

      if (resolved.properties) {
        const required = requiredFields || resolved.required || [];

        for (const [propName, propSchema] of Object.entries(resolved.properties)) {
          const propResolved = resolveSchema(propSchema);
          const isRequired = required.includes(propName);
          const fieldPath = prefix ? prefix + '.' + propName : propName;
          const propType = getFieldType(propResolved);
          const defaultValue = getFieldDefault(propResolved);

          html += '<div class="body-field-row">';
          html += '<div class="body-field-label">';
          html += escapeHtml(propName);
          if (isRequired) html += '<span class="required">*</span>';
          html += '<span class="field-type">' + escapeHtml(propType) + '</span>';
          html += '</div>';
          html += '<div class="body-field-input">';

          if (propResolved?.enum) {
            // Enum field - render as select
            html += '<select data-body-field="' + escapeHtml(fieldPath) + '">';
            html += '<option value="">-- Select --</option>';
            for (const opt of propResolved.enum) {
              const selected = defaultValue === opt ? ' selected' : '';
              html += '<option value="' + escapeHtml(String(opt)) + '"' + selected + '>' + escapeHtml(String(opt)) + '</option>';
            }
            html += '</select>';
          } else if (propResolved?.type === 'boolean') {
            // Boolean field - render as select
            html += '<select data-body-field="' + escapeHtml(fieldPath) + '">';
            html += '<option value="">-- Select --</option>';
            html += '<option value="true"' + (defaultValue === true ? ' selected' : '') + '>true</option>';
            html += '<option value="false"' + (defaultValue === false ? ' selected' : '') + '>false</option>';
            html += '</select>';
          } else if (propResolved?.type === 'object' && propResolved.properties) {
            // Nested object
            html += '<div class="nested-object">';
            html += '<div class="nested-object-label">' + escapeHtml(propName) + ' object</div>';
            html += renderBodyFields(propResolved, propResolved.required || [], fieldPath, depth + 1);
            html += '</div>';
          } else if (propResolved?.type === 'array') {
            // Array field: one input per item; object and array items are edited as JSON
            html += '<div class="array-field-container" data-array-field="' + escapeHtml(fieldPath) + '">';
            html += renderArrayItem(fieldPath, propResolved.items);
            html += '<button type="button" class="array-add-btn" data-add-item="' + escapeHtml(fieldPath) + '">' + icon('add') + 'Add item</button>';
            html += '</div>';
          } else if (propResolved?.type === 'integer' || propResolved?.type === 'number') {
            // Number field
            html += '<input type="number" data-body-field="' + escapeHtml(fieldPath) + '" ';
            html += 'placeholder="' + escapeHtml(propType) + '"';
            if (defaultValue !== undefined && defaultValue !== null) html += ' value="' + defaultValue + '"';
            html += '>';
          } else {
            // String or other field - render as input or textarea
            const isLongText = propResolved?.maxLength > 200 || propResolved?.format === 'text';
            if (isLongText) {
              html += '<textarea data-body-field="' + escapeHtml(fieldPath) + '" ';
              html += 'placeholder="' + escapeHtml(propType) + '">';
              html += defaultValue !== undefined ? escapeHtml(String(defaultValue)) : '';
              html += '</textarea>';
            } else {
              html += '<input type="text" data-body-field="' + escapeHtml(fieldPath) + '" ';
              html += 'placeholder="' + escapeHtml(propType) + '"';
              if (defaultValue !== undefined && defaultValue !== null) html += ' value="' + escapeHtml(String(defaultValue)) + '"';
              html += '>';
            }
          }

          if (propResolved?.description) {
            html += '<div class="body-field-desc md">' + renderMarkdown(propResolved.description) + '</div>';
          }

          html += '</div>';
          html += '</div>';
        }
      }

      html += '</div>';
      return html;
    }

    function renderArrayItem(fieldPath, itemsSchema) {
      const items = resolveSchema(itemsSchema) || {};
      const attr = 'data-array-item="' + escapeHtml(fieldPath) + '"';
      let input;
      if (items.type === 'object' || items.properties || items.type === 'array' || items.oneOf || items.anyOf) {
        input = '<textarea class="try-it-textarea" spellcheck="false" ' + attr + '>' + escapeHtml(JSON.stringify(generateExample(itemsSchema), null, 2)) + '</textarea>';
      } else {
        const example = items.example !== undefined ? String(items.example) : (items.enum ? items.enum.join(' | ') : getFieldType(items));
        input = '<input type="text" class="try-it-input" placeholder="' + escapeHtml(example) + '" ' + attr + '>';
      }
      return '<div class="array-item">' + input +
        '<button type="button" class="array-remove-btn" title="Remove item" aria-label="Remove item">' + icon('close') + '</button></div>';
    }

    // Render multipart form fields (for file uploads)
    function renderMultipartFields(schema, requiredFields) {
      if (!schema) return '<div class="body-field-desc">No schema available</div>';

      const resolved = resolveSchema(schema);
      if (!resolved) return '<div class="body-field-desc">No schema available</div>';

      let html = '<div class="body-field-group">';
      const required = requiredFields || resolved.required || [];

      if (resolved.properties) {
        for (const [propName, propSchema] of Object.entries(resolved.properties)) {
          const propResolved = resolveSchema(propSchema);
          const isRequired = required.includes(propName);
          const propFormat = propResolved?.format;
          const propType = propResolved?.type;

          html += '<div class="body-field-row">';
          html += '<div class="body-field-label">';
          html += escapeHtml(propName);
          if (isRequired) html += '<span class="required">*</span>';

          // Show appropriate type indicator
          if (propFormat === 'binary' || propType === 'file') {
            html += '<span class="field-type">file</span>';
          } else {
            html += '<span class="field-type">' + escapeHtml(propType || 'string') + '</span>';
          }
          html += '</div>';
          html += '<div class="body-field-input">';

          // Check if this is a file field
          if (propFormat === 'binary' || propType === 'file') {
            html += '<div class="file-upload-container">';
            html += '<input type="file" class="file-upload-input" id="fileUpload_' + escapeHtml(propName) + '" data-file-field="' + escapeHtml(propName) + '" onchange="handleFileSelect(this)">';
            html += '<div id="fileInfo_' + escapeHtml(propName) + '" class="file-selected-info" style="display: none;">';
            html += '<span><span class="file-name"></span><span class="file-size"></span></span>';
            html += '<button type="button" class="file-clear-btn" onclick="clearFileSelection(\\'' + escapeHtml(propName) + '\\')" aria-label="Clear file">' + icon('close') + '</button>';
            html += '</div>';
            html += '</div>';
          } else if (propResolved?.enum) {
            // Enum field - render as select
            html += '<select data-body-field="' + escapeHtml(propName) + '">';
            html += '<option value="">-- Select --</option>';
            for (const opt of propResolved.enum) {
              html += '<option value="' + escapeHtml(String(opt)) + '">' + escapeHtml(String(opt)) + '</option>';
            }
            html += '</select>';
          } else {
            // Regular text field
            html += '<input type="text" data-body-field="' + escapeHtml(propName) + '" placeholder="' + escapeHtml(propType || 'string') + '">';
          }

          if (propResolved?.description) {
            html += '<div class="body-field-desc md">' + renderMarkdown(propResolved.description) + '</div>';
          }

          html += '</div>';
          html += '</div>';
        }
      }

      html += '</div>';
      return html;
    }

    // Handle file selection
    function handleFileSelect(input) {
      const fieldName = input.dataset.fileField;
      const fileInfo = document.getElementById('fileInfo_' + fieldName);

      if (input.files && input.files[0]) {
        const file = input.files[0];
        const fileName = fileInfo.querySelector('.file-name');
        const fileSize = fileInfo.querySelector('.file-size');

        fileName.textContent = file.name;
        fileSize.textContent = ' (' + formatFileSize(file.size) + ')';
        fileInfo.style.display = 'flex';
      } else {
        fileInfo.style.display = 'none';
      }
    }

    // Clear file selection
    function clearFileSelection(fieldName) {
      const input = document.querySelector('[data-file-field="' + fieldName + '"]');
      const fileInfo = document.getElementById('fileInfo_' + fieldName);

      if (input) {
        input.value = '';
      }
      if (fileInfo) {
        fileInfo.style.display = 'none';
      }
    }

    // Format file size
    function formatFileSize(bytes) {
      if (bytes === 0) return '0 Bytes';
      const k = 1024;
      const sizes = ['Bytes', 'KB', 'MB', 'GB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    // Make file functions globally accessible
    window.handleFileSelect = handleFileSelect;
    window.clearFileSelection = clearFileSelection;

    // Get field type string
    function getFieldType(schema) {
      if (!schema) return 'any';
      if (schema.enum) return 'enum';
      if (schema.type === 'array') {
        const itemType = schema.items ? getFieldType(resolveSchema(schema.items)) : 'any';
        return 'array<' + itemType + '>';
      }
      return schema.type || 'object';
    }

    // Get field default value
    function getFieldDefault(schema) {
      if (!schema) return undefined;
      if (schema.default !== undefined) return schema.default;
      if (schema.example !== undefined) return schema.example;
      if (schema.enum && schema.enum.length > 0) return schema.enum[0];
      return undefined;
    }

    // Add or remove array items; the form is re-rendered per endpoint, so listen once on the panel
    document.addEventListener('click', (e) => {
      const add = e.target.closest('[data-add-item]');
      if (add) {
        const path = add.dataset.addItem;
        const schema = arrayItemsSchema(path);
        add.insertAdjacentHTML('beforebegin', renderArrayItem(path, schema));
        return;
      }
      const remove = e.target.closest('.array-remove-btn');
      if (remove) {
        const item = remove.closest('.array-item');
        const container = item.closest('.array-field-container');
        // Keep one input so the field stays visible; clearing it leaves the array out of the body
        if (container.querySelectorAll('.array-item').length > 1) item.remove();
        else item.querySelectorAll('input, textarea').forEach(el => { el.value = ''; });
      }
    });

    // The items schema of the body array at a dotted path, for "Add item"
    function arrayItemsSchema(path) {
      let schema = resolveSchema(currentEndpoint?.requestBody?.content?.['application/json']?.schema);
      for (const part of path.split('.')) {
        schema = resolveSchema(schema?.properties?.[part]);
      }
      return schema?.items;
    }


    // Fetch and render health status
    // Infrastructure from /api/v1/health, the Python services from /api/v1/health/services.
    async function fetchHealthStatus() {
      const get = (url) => fetch(url).then((r) => (r.ok ? r.json() : null)).catch(() => null);
      const [platform, python] = await Promise.all([get('/api/v1/health'), get('/api/v1/health/services')]);
      if (!platform && !python) return null;
      const services = [];
      const add = (group, names) => {
        for (const [key, status] of Object.entries((group && group.services) || {})) {
          services.push({ name: (names && names[key]) || key, status: status === 'healthy' || status === 'unhealthy' ? status : 'unknown' });
        }
      };
      add(platform, platform && platform.serviceNames);
      add(python, { query: 'Query', connector: 'Connectors', indexing: 'Indexing', docling: 'Docling', embedding: 'Embedding', parsing: 'Parsing', extraction: 'Extraction' });
      const platformDown = !platform || platform.status !== 'healthy';
      const anyDown = !python || services.some((svc) => svc.status !== 'healthy');
      return {
        overallStatus: platformDown ? 'unhealthy' : anyDown ? 'degraded' : 'healthy',
        timestamp: (platform && platform.timestamp) || (python && python.timestamp) || new Date().toISOString(),
        services,
      };
    }

    function renderHealthSection(healthData) {
      const header = (action) =>
        '<div class="health-header">' +
        '<div class="health-title"><span class="health-title-icon">' + icon('monitor_heart') + '</span><span>Service health</span></div>' +
        action +
        '</div>';
      const refresh = (label) =>
        '<button type="button" class="secondary-btn health-refresh-btn" onclick="refreshHealth()">' + icon('refresh', null, 16) + '<span>' + label + '</span></button>';

      if (healthData === undefined) {
        return '<div class="health-section">' + header('') +
          '<div class="health-loading"><div class="spinner"></div><span>Checking service health...</span></div></div>';
      }
      if (healthData === null) {
        return '<div class="health-section">' + header(refresh('Retry')) +
          '<div class="health-overall"><span class="health-overall-label">Status</span>' +
          '<span class="health-overall-status unhealthy">Unable to check</span></div></div>';
      }

      const label = healthData.overallStatus.charAt(0).toUpperCase() + healthData.overallStatus.slice(1);
      let html = '<div class="health-section">' + header(refresh('Refresh'));
      html += '<div class="health-overall">' +
        '<span class="health-overall-label">Overall status</span>' +
        '<span class="health-overall-status ' + escapeHtml(healthData.overallStatus) + '">' + escapeHtml(label) + '</span>' +
        '<span class="health-timestamp">Checked at ' + escapeHtml(new Date(healthData.timestamp).toLocaleTimeString()) + '</span>' +
        '</div>';

      html += '<div class="health-services">';
      for (const service of healthData.services) {
        html += '<div class="health-service-card">' +
          '<div class="health-service-indicator ' + escapeHtml(service.status) + '"></div>' +
          '<div class="health-service-info">' +
          '<div class="health-service-name">' + escapeHtml(service.name) + '</div>' +
          '<div class="health-service-details">' + escapeHtml(service.status) + '</div>' +
          '</div></div>';
      }
      html += '</div></div>';

      return html;
    }

    async function refreshHealth() {
      const btn = document.querySelector('.health-refresh-btn');
      if (btn) {
        btn.classList.add('loading');
      }

      const healthData = await fetchHealthStatus();
      const healthSection = document.querySelector('.health-section');
      if (healthSection) {
        healthSection.outerHTML = renderHealthSection(healthData);
      }
    }

    // Make refreshHealth globally accessible
    window.refreshHealth = refreshHealth;

    // Show welcome screen
    function showWelcomeScreen() {
      currentModule = null;
      currentEndpoint = null;

      // Clear active states
      document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.endpoint-list').forEach(el => el.style.display = 'none');

      document.getElementById('breadcrumb').innerHTML = '<span class="breadcrumb-current">API Reference</span>';
      hideCodePanel();

      const origin = window.location.origin;
      let html = '<div class="welcome-screen">' +
        '<div class="welcome-icon">' + LOGO_SVG + '</div>' +
        '<h1 class="welcome-title">' + escapeHtml(apiDocs?.info?.title || 'PipesHub API') + '</h1>' +
        '<p class="welcome-description">Every endpoint this PipesHub instance serves. Pick a module in the sidebar, or search, ' +
        'and send requests from the Try It panel with your own token.</p>' +
        '<div class="quickstart">' +
        '<div class="quickstart-card">' + icon('link') + '<h3>Base URL</h3><p><code>' + escapeHtml(origin) + '/api/v1</code></p></div>' +
        '<div class="quickstart-card">' + icon('key') + '<h3>Authentication</h3><p>Send <code>Authorization: Bearer &lt;token&gt;</code> with a personal access token or an OAuth access token.</p></div>' +
        '<div class="quickstart-card">' + icon('play_circle') + '<h3>Try It</h3><p>Requests go straight from your browser to the base URL. The token is kept in this browser only.</p></div>' +
        '</div>' +
        '</div>' +
        renderHealthSection(undefined);

      if (apiDocs?.info?.description) {
        html += '<div class="doc-section"><h2 class="section-title">Overview</h2><div class="md">' + renderMarkdown(apiDocs.info.description) + '</div></div>';
      }

      document.getElementById('docPanel').innerHTML = html;
      document.getElementById('docPanel').scrollTop = 0;

      fetchHealthStatus().then(healthData => {
        const healthSection = document.querySelector('.health-section');
        if (healthSection) {
          healthSection.outerHTML = renderHealthSection(healthData);
        }
      });
    }

    function hideCodePanel() {
      document.getElementById('codePanel').style.display = 'none';
      document.getElementById('codePanelResizeHandle').style.display = 'none';
    }

    // Select module
    function selectModule(moduleId) {
      const isSameModule = currentModule && currentModule.id === moduleId;
      const endpointList = document.querySelector('.endpoint-list[data-module="' + moduleId + '"]');
      const isCurrentlyExpanded = endpointList && endpointList.style.display === 'block';

      // If clicking on the same module that's already expanded, collapse it
      if (isSameModule && isCurrentlyExpanded) {
        endpointList.style.display = 'none';
        document.querySelector('.nav-item[data-module="' + moduleId + '"]')?.classList.remove('active');
        // Collapse all tag sections within this module
        document.querySelectorAll('.tag-section[data-module="' + moduleId + '"]').forEach(el => {
          el.classList.remove('expanded');
        });
        return;
      }

      currentModule = apiDocs.modules.find(m => m.id === moduleId);

      // Collapse all other endpoint lists and expand the selected one
      document.querySelectorAll('.endpoint-list').forEach(el => {
        el.style.display = el.dataset.module === moduleId ? 'block' : 'none';
      });

      // Collapse all tag sections in other modules
      document.querySelectorAll('.tag-section').forEach(el => {
        if (el.dataset.module !== moduleId) {
          el.classList.remove('expanded');
        }
      });

      // Update active state
      document.querySelectorAll('.nav-item').forEach(el => {
        el.classList.toggle('active', el.dataset.module === moduleId);
      });

      // Show module overview
      showModuleOverview(moduleId);
    }

    // Show module overview
    function showModuleOverview(moduleId) {
      const module = apiDocs.modules.find(m => m.id === moduleId);

      document.getElementById('breadcrumb').innerHTML =
        '<span class="breadcrumb-link" data-action="home">API Reference</span>' +
        '<span class="breadcrumb-separator">/</span>' +
        '<span class="breadcrumb-current">' + escapeHtml(module.name) + '</span>';
      hideCodePanel();

      let html = '<div class="endpoint-header">';
      html += '<div class="endpoint-title"><h1>' + escapeHtml(module.name) + '</h1></div>';
      if (module.description) {
        html += '<div class="endpoint-description md">' + renderMarkdown(module.description) + '</div>';
      }
      html += '</div>';

      html += '<div class="doc-section">';
      html += '<h2 class="section-title">Groups</h2>';
      html += '<div class="module-list">';
      for (const tag of module.tags) {
        const endpointCount = tagEndpoints(tag).length;
        html += '<div class="module-card" data-module-id="' + escapeHtml(moduleId) + '" data-tag="' + escapeHtml(tag) + '">';
        html += '<div class="module-card-icon">' + icon(moduleIconName(module.icon)) + '</div>';
        html += '<div class="module-card-header">';
        html += '<div class="module-card-title">' + escapeHtml(tag) + '</div>';
        html += '<div class="module-card-meta">' + endpointCount + (endpointCount === 1 ? ' endpoint' : ' endpoints') + '</div>';
        html += '</div>';
        html += icon('chevron_right', 'module-card-arrow');
        html += '</div>';
      }
      html += '</div>';
      html += '</div>';

      setDocPanel(html);
    }

    // Show tag endpoints
    function showTagEndpoints(moduleId, tag) {
      const module = apiDocs.modules.find(m => m.id === moduleId);
      const endpoints = tagEndpoints(tag);

      document.getElementById('breadcrumb').innerHTML =
        '<span class="breadcrumb-link" data-action="home">API Reference</span>' +
        '<span class="breadcrumb-separator">/</span>' +
        '<span class="breadcrumb-link" data-action="module" data-module-id="' + escapeHtml(moduleId) + '">' + escapeHtml(module.name) + '</span>' +
        '<span class="breadcrumb-separator">/</span>' +
        '<span class="breadcrumb-current">' + escapeHtml(tag) + '</span>';
      hideCodePanel();

      let html = '<div class="endpoint-header">';
      html += '<div class="endpoint-title"><h1>' + escapeHtml(tag) + '</h1></div>';
      html += '</div>';

      html += '<div class="doc-section">';
      html += '<h2 class="section-title">Endpoints <span class="section-subtitle">' + endpoints.length + '</span></h2>';
      html += '<div class="endpoint-rows">';
      for (const ep of endpoints) {
        html += '<div class="endpoint-row" data-endpoint="' + escapeHtml(ep.operationId) + '" data-tag="' + escapeHtml(tag) + '">';
        html += methodBadge(ep.method);
        html += '<span class="endpoint-row-summary">' + escapeHtml(ep.summary || ep.operationId) + '</span>';
        html += '<span class="endpoint-row-path">' + escapeHtml(ep.path) + '</span>';
        html += '</div>';
      }
      html += '</div>';
      html += '</div>';

      setDocPanel(html);
    }

    function setDocPanel(html) {
      const panel = document.getElementById('docPanel');
      panel.innerHTML = html;
      panel.scrollTop = 0;
      document.querySelector('.content-body').scrollTop = 0;
    }

    // Select endpoint
    function selectEndpoint(operationId, fromTag) {
      currentEndpoint = apiDocs.endpoints.find(e => e.operationId === operationId);
      if (!currentEndpoint) return;

      const tag = currentEndpoint.tags.includes(fromTag) ? fromTag : currentEndpoint.tags[0];
      const module = apiDocs.modules.find(m => m.tags.includes(tag));

      // Update active state
      document.querySelectorAll('.endpoint-item').forEach(el => {
        el.classList.toggle('active', el.dataset.endpoint === operationId);
      });

      let breadcrumb = '<span class="breadcrumb-link" data-action="home">API Reference</span>';
      if (module) {
        breadcrumb +=
          '<span class="breadcrumb-separator">/</span>' +
          '<span class="breadcrumb-link" data-action="module" data-module-id="' + escapeHtml(module.id) + '">' + escapeHtml(module.name) + '</span>' +
          '<span class="breadcrumb-separator">/</span>' +
          '<span class="breadcrumb-link" data-action="tag" data-module-id="' + escapeHtml(module.id) + '" data-tag="' + escapeHtml(tag) + '">' + escapeHtml(tag) + '</span>';
      }
      breadcrumb += '<span class="breadcrumb-separator">/</span>' +
        '<span class="breadcrumb-current">' + escapeHtml(currentEndpoint.summary || currentEndpoint.operationId) + '</span>';
      document.getElementById('breadcrumb').innerHTML = breadcrumb;

      document.getElementById('codePanel').style.display = 'flex';
      document.getElementById('codePanelResizeHandle').style.display = 'block';

      showEndpointDetails();
      setupTryItPanel();
      switchTab(currentTab);
      document.getElementById('codeContent').scrollTop = 0;
    }

    // The body media type the docs and Try It use: JSON when offered, else the first one listed
    function bodyContent(ep) {
      const content = ep.requestBody?.content || {};
      const type = content['application/json'] ? 'application/json' : Object.keys(content)[0];
      return type ? { type, schema: content[type].schema } : null;
    }

    function responseCodeClass(code) {
      const first = String(code).charAt(0);
      return ['2', '3', '4', '5'].includes(first) ? first + 'xx' : 'default';
    }

    // Show endpoint details
    function showEndpointDetails() {
      const ep = currentEndpoint;
      const isFullUrl = ep.basePath.startsWith('http://') || ep.basePath.startsWith('https://');

      let html = '<div class="endpoint-header">';
      html += '<div class="endpoint-title">';
      html += methodBadge(ep.method, 'large');
      html += '<h1>' + escapeHtml(ep.summary || ep.operationId) + '</h1>';
      html += '</div>';
      html += '<div class="endpoint-path">';
      html += '<span><span class="endpoint-path-base">' + escapeHtml(ep.basePath) + '</span>' + escapeHtml(ep.path) + '</span>';
      html += copyButton((isFullUrl ? '' : window.location.origin) + ep.basePath + ep.path);
      html += '</div>';
      if (ep.description) {
        html += '<div class="endpoint-description md">' + renderMarkdown(ep.description) + '</div>';
      }
      html += '</div>';

      // Parameters
      if (ep.parameters && ep.parameters.length > 0) {
        html += '<div class="doc-section">';
        html += '<h2 class="section-title">Parameters</h2>';
        html += '<table class="params-table">';
        html += '<thead><tr><th>Name</th><th>Description</th></tr></thead>';
        html += '<tbody>';
        for (const param of ep.parameters) {
          const schema = resolveSchema(param.schema) || {};
          html += '<tr>';
          html += '<td style="width: 32%;"><span class="param-name">' + escapeHtml(param.name) + '</span>';
          if (param.required) html += '<span class="param-required">required</span>';
          html += '<span class="param-in">' + escapeHtml(param.in) + ' · <span class="param-type">' + escapeHtml(getSchemaType(schema)) + '</span></span>';
          html += '</td>';
          html += '<td><div class="md small">' + (param.description ? renderMarkdown(param.description) : '<span style="color: var(--text-3);">No description</span>') + '</div>';
          if (schema.enum) {
            html += '<div class="schema-field-enum">One of';
            for (const val of schema.enum) html += '<span class="schema-field-enum-value">' + escapeHtml(String(val)) + '</span>';
            html += '</div>';
          }
          html += '</td>';
          html += '</tr>';
        }
        html += '</tbody></table>';
        html += '</div>';
      }

      // Request Body
      const body = ep.requestBody ? bodyContent(ep) : null;
      if (ep.requestBody) {
        html += '<div class="doc-section">';
        html += '<h2 class="section-title">Request body' +
          (ep.requestBody.required ? '<span class="param-required">required</span>' : '') +
          (body ? '<span class="content-type">' + escapeHtml(body.type) + '</span>' : '') + '</h2>';
        if (ep.requestBody.description && ep.requestBody.description !== 'Request payload') {
          html += '<div class="md section-note">' + renderMarkdown(ep.requestBody.description) + '</div>';
        }
        if (body?.schema) {
          const schemaName = body.schema.$ref ? body.schema.$ref.replace('#/components/schemas/', '') : 'Body';
          html += renderSchema(body.schema, schemaName);
        }
        html += '</div>';
      }

      // Responses
      if (ep.responses) {
        html += '<div class="doc-section">';
        html += '<h2 class="section-title">Responses</h2>';
        for (const [code, response] of Object.entries(ep.responses)) {
          html += '<div class="response-item">';
          html += '<div class="response-header">';
          html += '<span class="response-code response-code-' + responseCodeClass(code) + '">' + escapeHtml(code) + '</span>';
          html += '<div class="md small">' + renderMarkdown(response.description || '') + '</div>';
          html += '</div>';

          const content = response.content || {};
          const type = content['application/json'] ? 'application/json' : Object.keys(content)[0];
          const responseSchema = type && content[type].schema;
          if (responseSchema) {
            const schemaName = responseSchema.$ref
              ? responseSchema.$ref.replace('#/components/schemas/', '')
              : (type === 'application/json' ? 'Response body' : type);
            html += renderSchema(responseSchema, schemaName);
          }
          html += '</div>';
        }
        html += '</div>';
      }

      setDocPanel(html);
    }

    // Update code example
    function updateCodeExample(lang) {
      if (!currentEndpoint) return;
      const ep = currentEndpoint;
      const configuredBaseUrl = document.getElementById('baseUrlInput')?.value || config.baseUrl || window.location.origin;
      const isFullUrl = ep.basePath.startsWith('http://') || ep.basePath.startsWith('https://');
      const url = (isFullUrl ? ep.basePath : configuredBaseUrl + ep.basePath) + ep.path;
      const body = ep.requestBody ? bodyContent(ep) : null;
      const isMultipart = body?.type === 'multipart/form-data';
      const example = body?.schema ? generateExample(body.schema) : {};
      const bodyExample = JSON.stringify(example, null, 2);
      const fileFields = isMultipart ? Object.entries(resolveSchema(body.schema)?.properties || {})
        .filter(([, prop]) => { const p = resolveSchema(prop) || {}; return p.format === 'binary' || p.type === 'file'; })
        .map(([name]) => name) : [];
      const formFields = isMultipart ? Object.entries(example).filter(([name]) => !fileFields.includes(name)) : [];
      const indent = (text, pad) => text.split('\\n').join('\\n' + pad);

      let code = '';
      if (lang === 'curl') {
        code = 'curl -X ' + ep.method + ' "' + url + '" \\\\\\n  -H "Authorization: Bearer YOUR_TOKEN"';
        if (isMultipart) {
          for (const name of fileFields) code += ' \\\\\\n  -F "' + name + '=@/path/to/file"';
          for (const [name, value] of formFields) code += ' \\\\\\n  -F "' + name + '=' + (typeof value === 'object' ? JSON.stringify(value) : value) + '"';
        } else if (body) {
          code += ' \\\\\\n  -H "Content-Type: application/json" \\\\\\n  -d \\'' + bodyExample.replace(/'/g, "'\\\\''") + '\\'';
        }
      } else if (lang === 'javascript') {
        if (isMultipart) {
          code = 'const form = new FormData();\\n';
          for (const name of fileFields) code += 'form.append("' + name + '", fileInput.files[0]);\\n';
          for (const [name, value] of formFields) code += 'form.append("' + name + '", ' + JSON.stringify(typeof value === 'object' ? JSON.stringify(value) : String(value)) + ');\\n';
          code += '\\n';
        }
        code += 'const response = await fetch("' + url + '", {\\n  method: "' + ep.method + '",\\n  headers: {\\n    "Authorization": "Bearer YOUR_TOKEN"';
        if (body && !isMultipart) code += ',\\n    "Content-Type": "application/json"';
        code += '\\n  }';
        if (isMultipart) code += ',\\n  body: form';
        else if (body) code += ',\\n  body: JSON.stringify(' + indent(bodyExample, '  ') + ')';
        code += '\\n});\\n\\nconst data = await response.json();';
      } else if (lang === 'python') {
        code = 'import requests\\n\\nresponse = requests.' + ep.method.toLowerCase() + '(\\n    "' + url + '",\\n    headers={"Authorization": "Bearer YOUR_TOKEN"}';
        if (isMultipart) {
          code += ',\\n    files={' + fileFields.map(name => '"' + name + '": open("/path/to/file", "rb")').join(', ') + '}';
          if (formFields.length) code += ',\\n    data={' + formFields.map(([name, value]) => '"' + name + '": ' + JSON.stringify(typeof value === 'object' ? JSON.stringify(value) : String(value))).join(', ') + '}';
        } else if (body) {
          code += ',\\n    json=' + indent(toPython(example), '    ');
        }
        code += ',\\n)\\n\\ndata = response.json()';
      } else if (lang === 'requestbody') {
        document.getElementById('codeExamplesPanel').innerHTML =
          '<div class="code-block">' +
          '<div class="code-block-header">' +
          '<span class="code-block-title">Request body (JSON)</span>' +
          '<button class="copy-btn" id="copyBtn" type="button">' + icon('content_copy') + '<span class="copy-label">Copy</span></button>' +
          '</div>' +
          '<textarea id="codeBodyTextarea" class="raw-json-textarea" spellcheck="false">' + escapeHtml(bodyExample) + '</textarea>' +
          '<div id="codeBodyError" class="raw-json-error" style="display: none; padding: 0 12px 12px;"></div>' +
          '<div class="code-block-footer">' +
          '<button class="primary-btn" id="useInTryItBtn" type="button">' + icon('play_arrow') + 'Use in Try It</button>' +
          '</div>' +
          '</div>';

        document.getElementById('copyBtn').onclick = function() {
          copyText(document.getElementById('codeBodyTextarea').value, this);
        };

        document.getElementById('useInTryItBtn').onclick = function() {
          const textarea = document.getElementById('codeBodyTextarea');
          const errorDiv = document.getElementById('codeBodyError');
          try {
            JSON.parse(textarea.value);
            document.getElementById('rawJsonTextarea').value = textarea.value;
            toggleInputMode('raw');
            document.querySelectorAll('.code-tab').forEach(t => t.classList.remove('active'));
            document.querySelector('.code-tab[data-tab="tryit"]').classList.add('active');
            currentTab = 'tryit';
            switchTab('tryit');
          } catch (e) {
            errorDiv.textContent = 'Invalid JSON: ' + e.message;
            errorDiv.style.display = 'block';
          }
        };

        document.getElementById('codeBodyTextarea').addEventListener('input', function() {
          const errorDiv = document.getElementById('codeBodyError');
          try {
            if (this.value.trim()) JSON.parse(this.value);
            this.classList.remove('error');
            errorDiv.style.display = 'none';
          } catch (e) {
            this.classList.add('error');
            errorDiv.textContent = 'Invalid JSON: ' + e.message;
            errorDiv.style.display = 'block';
          }
        });
        return;
      }

      const titles = { curl: 'cURL', javascript: 'JavaScript', python: 'Python' };
      document.getElementById('codeExamplesPanel').innerHTML =
        '<div class="code-block">' +
        '<div class="code-block-header">' +
        '<span class="code-block-title">' + titles[lang] + '</span>' +
        '<button class="copy-btn" id="copyBtn" type="button">' + icon('content_copy') + '<span class="copy-label">Copy</span></button>' +
        '</div>' +
        '<pre>' + escapeHtml(code) + '</pre>' +
        '</div>';

      document.getElementById('copyBtn').onclick = function() {
        copyText(code, this);
      };
    }

    // JSON as a Python literal: true/false/null become True/False/None outside strings
    function toPython(value) {
      return JSON.stringify(value, null, 2).replace(/("(?:[^"\\\\]|\\\\.)*")|\\b(true|false|null)\\b/g,
        (match, str, word) => str || { true: 'True', false: 'False', null: 'None' }[word]);
    }

    // Filter endpoints
    function filterEndpoints(query) {
      const q = query.toLowerCase().trim();

      // If empty query, reset to default state
      if (!q) {
        document.querySelectorAll('.endpoint-list').forEach(el => {
          el.style.display = 'none';
        });
        document.querySelectorAll('.tag-section').forEach(el => {
          el.classList.remove('expanded');
          el.style.display = 'block';
        });
        document.querySelectorAll('.endpoint-item').forEach(el => {
          el.style.display = 'flex';
        });
        document.querySelectorAll('.nav-item').forEach(el => {
          el.style.display = 'flex';
        });
        document.querySelectorAll('.nav-category').forEach(el => {
          el.style.display = 'block';
        });
        document.getElementById('searchEmpty').style.display = 'none';
        // Re-open the module being viewed
        if (currentModule) {
          const list = document.querySelector('.endpoint-list[data-module="' + currentModule.id + '"]');
          if (list) list.style.display = 'block';
        }
        return;
      }

      // Track which modules and tags have visible endpoints
      const visibleModules = new Set();
      const visibleTags = new Set();

      // Filter endpoint items
      document.querySelectorAll('.endpoint-item').forEach(el => {
        const text = el.textContent.toLowerCase();
        const operationId = el.dataset.endpoint || '';
        const endpoint = apiDocs.endpoints.find(e => e.operationId === operationId);

        // Search in text, path, and tags
        const matches = text.includes(q) ||
          (endpoint && endpoint.path.toLowerCase().includes(q)) ||
          operationId.toLowerCase().includes(q) ||
          (endpoint && endpoint.tags.some(t => t.toLowerCase().includes(q)));

        if (matches) {
          el.style.display = 'flex';
          // Find parent tag section and module
          const tagSection = el.closest('.tag-section');
          const endpointList = el.closest('.endpoint-list');
          if (tagSection) {
            visibleTags.add(tagSection);
          }
          if (endpointList && endpointList.dataset.module) {
            visibleModules.add(endpointList.dataset.module);
          }
        } else {
          el.style.display = 'none';
        }
      });

      // Show/hide tag sections based on whether they have visible endpoints
      document.querySelectorAll('.tag-section').forEach(el => {
        if (visibleTags.has(el)) {
          el.style.display = 'block';
          el.classList.add('expanded');
        } else {
          el.style.display = 'none';
        }
      });

      // Show/hide endpoint lists based on whether their module has visible endpoints
      document.querySelectorAll('.endpoint-list').forEach(el => {
        if (visibleModules.has(el.dataset.module)) {
          el.style.display = 'block';
        } else {
          el.style.display = 'none';
        }
      });

      // Show/hide nav items (modules) based on whether they have visible endpoints
      document.querySelectorAll('.nav-item').forEach(el => {
        if (visibleModules.has(el.dataset.module)) {
          el.style.display = 'flex';
        } else {
          el.style.display = 'none';
        }
      });

      // Show/hide categories based on whether they have any visible modules
      document.querySelectorAll('.nav-category').forEach(el => {
        const hasVisibleModules = Array.from(el.querySelectorAll('.nav-item')).some(
          navItem => navItem.style.display !== 'none'
        );
        el.style.display = hasVisibleModules ? 'block' : 'none';
      });
      document.getElementById('searchEmpty').style.display = visibleModules.size === 0 ? 'block' : 'none';
    }

    // Escape HTML; also used for attribute values, so quotes are escaped too
    function escapeHtml(text) {
      if (text === null || text === undefined) return '';
      return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    // The CommonMark subset the spec's descriptions use: paragraphs, headings, lists, fenced code,
    // inline code, bold, italics and links. The text is escaped first and only this markup is added back.
    function renderMarkdown(text) {
      if (!text) return '';
      const lines = String(text).replace(/\\r\\n?/g, '\\n').split('\\n');
      const out = [];
      let paragraph = [];
      let list = null;

      const flushParagraph = () => {
        if (paragraph.length) out.push('<p>' + renderInline(paragraph.join(' ')) + '</p>');
        paragraph = [];
      };
      const flushList = () => {
        if (list) out.push('<' + list.tag + '>' + list.items.map(item => '<li>' + renderInline(item.join(' ')) + '</li>').join('') + '</' + list.tag + '>');
        list = null;
      };

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        const fence = line.match(/^\\s*(\`\`\`|~~~)/);
        if (fence) {
          flushParagraph();
          flushList();
          const code = [];
          while (++i < lines.length && !lines[i].trim().startsWith(fence[1])) code.push(lines[i]);
          out.push('<pre><code>' + escapeHtml(code.join('\\n')) + '</code></pre>');
          continue;
        }
        if (!line.trim()) {
          flushParagraph();
          flushList();
          continue;
        }
        const heading = line.match(/^\\s{0,3}(#{1,6})\\s+(.*)$/);
        if (heading) {
          flushParagraph();
          flushList();
          const level = Math.min(heading[1].length + 2, 5);
          out.push('<h' + level + '>' + renderInline(heading[2]) + '</h' + level + '>');
          continue;
        }
        const item = line.match(/^\\s{0,3}([-*+]|\\d+[.)])\\s+(.*)$/);
        if (item) {
          flushParagraph();
          const tag = /\\d/.test(item[1]) ? 'ol' : 'ul';
          if (list && list.tag !== tag) flushList();
          if (!list) list = { tag, items: [] };
          list.items.push([item[2]]);
          continue;
        }
        // An indented line continues the current list item; a plain line after a list ends it
        if (list && /^\\s+/.test(line)) {
          list.items[list.items.length - 1].push(line.trim());
          continue;
        }
        flushList();
        paragraph.push(line.trim());
      }
      flushParagraph();
      flushList();
      return out.join('');
    }

    function renderInline(text) {
      const codes = [];
      // Code spans first, so nothing inside them is formatted
      let html = escapeHtml(text).replace(/\`([^\`]+)\`/g, (m, code) => {
        codes.push('<code>' + code + '</code>');
        return '\\u0000' + (codes.length - 1) + '\\u0000';
      });
      html = html
        .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
        .replace(/__(.+?)__/g, '<strong>$1</strong>')
        .replace(/(^|[^*\\w])\\*(?!\\s)([^*]+?)\\*(?!\\w)/g, '$1<em>$2</em>')
        .replace(/(^|[^\\w])_(?!\\s)([^_]+?)_(?!\\w)/g, '$1<em>$2</em>')
        .replace(/\\[([^\\]]+)\\]\\(((?:https?:\\/\\/|\\/|#)[^\\s)]*)\\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
      return html.replace(/\\u0000(\\d+)\\u0000/g, (m, i) => codes[Number(i)]);
    }
  </script>
</body>
</html>`;
}
