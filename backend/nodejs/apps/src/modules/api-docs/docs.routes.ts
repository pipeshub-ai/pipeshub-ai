/**
 * API Documentation Routes
 * Serves the API documentation HTML UI
 */
import { Router, Request, Response } from 'express';
import { Container } from 'inversify';
import { ApiDocsService } from './docs.service';

export function createApiDocsRouter(container: Container): Router {
  const router = Router();
  const apiDocsService = container.get<ApiDocsService>(ApiDocsService);

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
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Manrope:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-primary: #ffffff;
      --bg-secondary: #f8fafc;
      --bg-tertiary: #f1f5f9;
      --bg-sidebar: #f8fafc;
      --text-primary: #0f172a;
      --text-secondary: #475569;
      --text-muted: #94a3b8;
      --border-color: #e2e8f0;
      --accent-primary: #00ac3a;
      --accent-blue: #0099ff;
      --accent-green: #00ac3a;
      --accent-orange: #f97316;
      --accent-red: #ef4444;
      --accent-purple: #8b5cf6;
      --method-get: #00ac3a;
      --method-post: #0099ff;
      --method-put: #f97316;
      --method-patch: #8b5cf6;
      --method-delete: #ef4444;
      --code-bg: #1f1f1f;
      --code-text: #e2e8f0;
      --scrollbar-bg: #f1f5f9;
      --scrollbar-thumb: #cbd5e1;
    }

    [data-theme="dark"] {
      --bg-primary: #0a0a0a;
      --bg-secondary: #1f1f1f;
      --bg-tertiary: #2e2e2e;
      --bg-sidebar: #1f1f1f;
      --text-primary: #f8fafc;
      --text-secondary: #cbd5e1;
      --text-muted: #6b7280;
      --border-color: #2e2e2e;
      --code-bg: #0a0a0a;
      --code-text: #e2e8f0;
      --scrollbar-bg: #1f1f1f;
      --scrollbar-thumb: #404040;
    }

    [data-theme="dark"] .method-get { background: rgba(0, 172, 58, 0.15); color: #00d147; }
    [data-theme="dark"] .method-post { background: rgba(0, 153, 255, 0.15); color: #3db5ff; }
    [data-theme="dark"] .method-put { background: rgba(249, 115, 22, 0.15); color: #fdba74; }
    [data-theme="dark"] .method-patch { background: rgba(139, 92, 246, 0.15); color: #c4b5fd; }
    [data-theme="dark"] .method-delete { background: rgba(239, 68, 68, 0.15); color: #fca5a5; }

    [data-theme="dark"] .response-code-2xx { background: rgba(0, 172, 58, 0.15); color: #00d147; }
    [data-theme="dark"] .response-code-4xx { background: rgba(249, 115, 22, 0.15); color: #fdba74; }
    [data-theme="dark"] .response-code-5xx { background: rgba(239, 68, 68, 0.15); color: #fca5a5; }

    [data-theme="dark"] .param-required { background: rgba(239, 68, 68, 0.15); color: #fca5a5; }

    [data-theme="dark"] .response-status.success { background: rgba(0, 172, 58, 0.15); color: #00d147; }
    [data-theme="dark"] .response-status.error { background: rgba(239, 68, 68, 0.15); color: #fca5a5; }

    [data-theme="dark"] .module-card {
      background: var(--bg-secondary);
      border-color: var(--border-color);
    }

    [data-theme="dark"] .module-card:hover {
      border-color: var(--accent-green);
      box-shadow: 0 4px 12px rgba(0, 172, 58, 0.1);
    }

    [data-theme="dark"] .logo-icon {
      background: var(--bg-tertiary);
    }

    [data-theme="dark"] .search-input {
      color: var(--text-primary);
    }

    [data-theme="dark"] .search-input::placeholder {
      color: var(--text-muted);
    }

    [data-theme="dark"] .params-table th {
      background: var(--bg-tertiary);
    }

    [data-theme="dark"] .response-header {
      background: var(--bg-tertiary);
    }

    [data-theme="dark"] .code-block {
      background: rgba(0,0,0,0.4);
    }

    [data-theme="dark"] .code-block-header {
      background: rgba(0,0,0,0.3);
    }

    [data-theme="dark"] pre {
      background: transparent;
    }

    [data-theme="dark"] .module-card-icon {
      background: var(--bg-tertiary);
    }

    [data-theme="dark"] .tag-count {
      background: var(--bg-secondary);
    }

    [data-theme="dark"] .endpoint-path {
      background: var(--bg-tertiary);
    }

    [data-theme="dark"] .try-it-input,
    [data-theme="dark"] .try-it-textarea {
      background: rgba(0,0,0,0.4);
      border-color: rgba(255,255,255,0.15);
    }

    [data-theme="dark"] .response-item {
      border-color: var(--border-color);
    }

    * {
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }

    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      line-height: 1.6;
    }

    /* Scrollbar styles */
    ::-webkit-scrollbar {
      width: 8px;
      height: 8px;
    }

    ::-webkit-scrollbar-track {
      background: var(--scrollbar-bg);
    }

    ::-webkit-scrollbar-thumb {
      background: var(--scrollbar-thumb);
      border-radius: 4px;
    }

    ::-webkit-scrollbar-thumb:hover {
      background: #94a3b8;
    }

    [data-theme="dark"] ::-webkit-scrollbar-thumb:hover {
      background: #64748b;
    }

    /* Layout */
    .app-container {
      display: flex;
      height: 100vh;
    }

    /* Sidebar */
    .sidebar {
      width: 280px;
      min-width: 200px;
      max-width: 500px;
      background: var(--bg-sidebar);
      border-right: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
    }

    /* Resize handles */
    .resize-handle {
      width: 6px;
      background: transparent;
      cursor: col-resize;
      flex-shrink: 0;
      position: relative;
      z-index: 10;
      transition: background 0.15s;
    }

    .resize-handle:hover,
    .resize-handle.dragging {
      background: var(--accent-green);
    }

    .resize-handle::before {
      content: '';
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      width: 2px;
      height: 40px;
      background: var(--border-color);
      border-radius: 1px;
      opacity: 0;
      transition: opacity 0.15s;
    }

    .resize-handle:hover::before,
    .resize-handle.dragging::before {
      opacity: 1;
      background: white;
    }

    .resize-handle-right {
      margin-left: -3px;
      margin-right: -3px;
    }

    .resize-handle-left {
      margin-left: -3px;
      margin-right: -3px;
    }

    /* Prevent text selection while resizing */
    body.resizing {
      user-select: none;
      cursor: col-resize;
    }

    body.resizing * {
      cursor: col-resize !important;
    }

    .sidebar-header {
      padding: 20px;
      border-bottom: 1px solid var(--border-color);
    }

    .logo {
      display: flex;
      align-items: center;
      gap: 10px;
      font-family: 'Manrope', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      font-weight: 700;
      font-size: 18px;
      color: var(--text-primary);
      text-decoration: none;
    }

    .logo-icon {
      width: 32px;
      height: 32px;
      background: var(--bg-primary);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .logo-icon svg {
      width: 24px;
      height: 24px;
    }

    .search-box {
      margin-top: 16px;
      position: relative;
    }

    .search-input {
      width: 100%;
      padding: 10px 12px 10px 36px;
      border: 1px solid var(--border-color);
      border-radius: 8px;
      font-size: 14px;
      background: var(--bg-primary);
      color: var(--text-primary);
      outline: none;
      transition: border-color 0.2s;
    }

    .search-input::placeholder {
      color: var(--text-muted);
    }

    .search-input:focus {
      border-color: var(--accent-green);
    }

    .search-icon {
      position: absolute;
      left: 12px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-muted);
    }

    .sidebar-nav {
      flex: 1;
      overflow-y: auto;
      padding: 16px 0;
    }

    .nav-category {
      margin-bottom: 8px;
    }

    .category-header {
      padding: 8px 20px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
    }

    .nav-item {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 20px;
      cursor: pointer;
      transition: background 0.15s;
      text-decoration: none;
      color: var(--text-secondary);
      font-size: 14px;
    }

    .nav-item:hover {
      background: var(--bg-tertiary);
      color: var(--text-primary);
    }

    .nav-item.active {
      background: var(--bg-tertiary);
      color: var(--accent-green);
      font-weight: 500;
    }

    .nav-item-icon {
      width: 18px;
      height: 18px;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .endpoint-item {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 20px 6px 40px;
      cursor: pointer;
      transition: background 0.15s;
      font-size: 13px;
      color: var(--text-secondary);
    }

    .endpoint-item:hover {
      background: var(--bg-tertiary);
    }

    .endpoint-item.active {
      background: var(--bg-tertiary);
      color: var(--text-primary);
    }

    /* Tag sections in sidebar */
    .tag-section {
      margin-bottom: 4px;
    }

    .tag-header {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 6px 20px 6px 28px;
      font-size: 12px;
      font-weight: 500;
      color: var(--text-muted);
      cursor: pointer;
      transition: all 0.15s;
    }

    .tag-header .tag-count {
      margin-left: auto;
    }

    .tag-header:hover {
      color: var(--text-secondary);
      background: var(--bg-tertiary);
    }

    .tag-name {
      text-transform: capitalize;
    }

    .tag-count {
      background: var(--bg-tertiary);
      padding: 2px 6px;
      border-radius: 10px;
      font-size: 10px;
      font-weight: 600;
    }

    .tag-endpoints {
      display: none;
    }

    .tag-section.expanded .tag-endpoints {
      display: block;
    }

    .tag-section.expanded .tag-header {
      color: var(--text-primary);
    }

    .tag-chevron {
      transition: transform 0.2s;
      flex-shrink: 0;
    }

    .tag-section.expanded .tag-chevron {
      transform: rotate(90deg);
    }

    /* Breadcrumb clickable styles */
    .breadcrumb-link {
      cursor: pointer;
      transition: color 0.15s;
    }

    .breadcrumb-link:hover {
      color: var(--accent-green);
    }

    /* Theme toggle */
    .theme-toggle {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      cursor: pointer;
      color: var(--text-secondary);
      font-size: 13px;
      transition: all 0.2s;
    }

    .theme-toggle:hover {
      background: var(--border-color);
      color: var(--text-primary);
    }

    .theme-toggle svg {
      width: 16px;
      height: 16px;
    }

    .theme-toggle .sun-icon { display: none; }
    .theme-toggle .moon-icon { display: block; }

    [data-theme="dark"] .theme-toggle .sun-icon { display: block; }
    [data-theme="dark"] .theme-toggle .moon-icon { display: none; }

    /* Method badges */
    .method-badge {
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      font-family: 'JetBrains Mono', monospace;
    }

    .method-get { background: rgba(0, 172, 58, 0.12); color: #00862d; }
    .method-post { background: rgba(0, 153, 255, 0.12); color: #0077cc; }
    .method-put { background: rgba(249, 115, 22, 0.12); color: #9a3412; }
    .method-patch { background: rgba(139, 92, 246, 0.12); color: #5b21b6; }
    .method-delete { background: rgba(239, 68, 68, 0.12); color: #991b1b; }

    /* Main content */
    .main-content {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    .content-header {
      padding: 16px 24px;
      border-bottom: 1px solid var(--border-color);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .breadcrumb {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 14px;
      color: var(--text-muted);
    }

    .breadcrumb-separator {
      color: var(--border-color);
    }

    .breadcrumb-current {
      color: var(--text-primary);
      font-weight: 500;
    }

    .content-body {
      flex: 1;
      display: flex;
      overflow: hidden;
    }

    /* Documentation panel */
    .doc-panel {
      flex: 1;
      overflow-y: auto;
      padding: 32px;
    }

    .endpoint-header {
      margin-bottom: 24px;
    }

    .endpoint-title {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 8px;
    }

    .endpoint-title h1 {
      font-family: 'Manrope', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      font-size: 24px;
      font-weight: 600;
    }

    .endpoint-path {
      font-family: 'JetBrains Mono', monospace;
      font-size: 14px;
      color: var(--text-secondary);
      background: var(--bg-secondary);
      padding: 8px 12px;
      border-radius: 6px;
      margin-top: 12px;
    }

    .endpoint-description {
      color: var(--text-secondary);
      font-size: 14px;
      margin-top: 16px;
      line-height: 1.7;
    }

    .endpoint-description b {
      color: var(--text-primary);
      font-weight: 600;
      display: inline-block;
      margin-top: 12px;
    }

    .endpoint-description code {
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      background: var(--bg-tertiary);
      color: var(--accent-purple);
      padding: 2px 6px;
      border-radius: 4px;
    }

    .endpoint-description pre {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      background: var(--code-bg);
      color: var(--code-text);
      padding: 12px 16px;
      border-radius: 6px;
      overflow-x: auto;
      margin: 8px 0;
      white-space: pre-wrap;
    }

    .endpoint-description ul,
    .endpoint-description ol {
      margin: 8px 0;
      padding-left: 24px;
    }

    .endpoint-description li {
      margin: 4px 0;
    }

    /* Sections */
    .doc-section {
      margin-top: 32px;
    }

    .section-title {
      font-size: 16px;
      font-weight: 600;
      margin-bottom: 16px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--border-color);
    }

    /* Parameters table */
    .params-table {
      width: 100%;
      border-collapse: collapse;
    }

    .params-table th,
    .params-table td {
      padding: 12px 16px;
      text-align: left;
      border-bottom: 1px solid var(--border-color);
    }

    .params-table th {
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
      background: var(--bg-secondary);
    }

    .param-name {
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      color: var(--accent-blue);
    }

    .param-type {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: var(--text-muted);
    }

    .param-required {
      display: inline-block;
      padding: 2px 6px;
      background: rgba(239, 68, 68, 0.12);
      color: #991b1b;
      font-size: 10px;
      font-weight: 600;
      border-radius: 4px;
      margin-left: 8px;
    }

    /* Response section */
    .response-item {
      margin-bottom: 16px;
      border: 1px solid var(--border-color);
      border-radius: 8px;
      overflow: hidden;
    }

    .response-header {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 12px 16px;
      background: var(--bg-secondary);
      cursor: pointer;
    }

    .response-code {
      font-family: 'JetBrains Mono', monospace;
      font-weight: 600;
      padding: 4px 8px;
      border-radius: 4px;
    }

    .response-code-2xx { background: rgba(0, 172, 58, 0.12); color: #00862d; }
    .response-code-4xx { background: rgba(249, 115, 22, 0.12); color: #9a3412; }
    .response-code-5xx { background: rgba(239, 68, 68, 0.12); color: #991b1b; }

    .response-body {
      padding: 16px;
      display: none;
    }

    .response-item.expanded .response-body {
      display: block;
    }

    /* Code panel */
    .code-panel {
      width: 480px;
      min-width: 300px;
      max-width: 800px;
      background: var(--code-bg);
      border-left: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      flex-shrink: 0;
    }

    .code-panel-header {
      padding: 16px;
      border-bottom: 1px solid rgba(255,255,255,0.1);
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .code-tab {
      padding: 6px 12px;
      font-size: 13px;
      color: var(--code-text);
      opacity: 0.6;
      cursor: pointer;
      border-radius: 6px;
      transition: all 0.15s;
    }

    .code-tab:hover {
      opacity: 0.8;
    }

    .code-tab.active {
      background: rgba(255,255,255,0.1);
      opacity: 1;
    }

    .code-content {
      flex: 1;
      overflow: auto;
      padding: 16px;
    }

    .code-block {
      background: rgba(0,0,0,0.2);
      border-radius: 8px;
      overflow: hidden;
    }

    .code-block-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 16px;
      background: rgba(0,0,0,0.2);
      border-bottom: 1px solid rgba(255,255,255,0.1);
    }

    .code-block-title {
      font-size: 12px;
      color: var(--code-text);
      opacity: 0.6;
    }

    .copy-btn {
      background: none;
      border: none;
      color: var(--code-text);
      opacity: 0.6;
      cursor: pointer;
      padding: 4px 8px;
      font-size: 12px;
      border-radius: 4px;
      transition: all 0.15s;
    }

    .copy-btn:hover {
      background: rgba(255,255,255,0.1);
      opacity: 1;
    }

    pre {
      margin: 0;
      padding: 16px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      line-height: 1.6;
      color: var(--code-text);
      overflow-x: auto;
    }

    code {
      font-family: 'JetBrains Mono', monospace;
    }

    /* Try It Panel Styles */
    .try-it-panel {
      padding: 8px;
    }

    .try-it-section {
      margin-bottom: 16px;
    }

    .try-it-label {
      display: block;
      font-size: 12px;
      font-weight: 600;
      color: var(--code-text);
      opacity: 0.8;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .try-it-input {
      width: 100%;
      padding: 10px 12px;
      background: rgba(0,0,0,0.3);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 6px;
      color: var(--code-text);
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      outline: none;
      transition: border-color 0.2s;
    }

    .try-it-input:focus {
      border-color: var(--accent-green);
    }

    .try-it-input::placeholder {
      color: rgba(255,255,255,0.3);
    }

    .try-it-textarea {
      width: 100%;
      min-height: 150px;
      padding: 12px;
      background: rgba(0,0,0,0.3);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 6px;
      color: var(--code-text);
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      outline: none;
      resize: vertical;
      transition: border-color 0.2s;
    }

    .try-it-textarea:focus {
      border-color: var(--accent-green);
    }

    .file-upload-container {
      width: 100%;
      position: relative;
    }

    .file-upload-input {
      width: 100%;
      padding: 20px;
      background: rgba(0,0,0,0.3);
      border: 2px dashed rgba(255,255,255,0.2);
      border-radius: 8px;
      color: var(--code-text);
      font-family: 'Inter', sans-serif;
      font-size: 14px;
      outline: none;
      cursor: pointer;
      transition: all 0.2s;
    }

    .file-upload-input:hover {
      border-color: var(--accent-green);
      background: rgba(0, 172, 58, 0.1);
    }

    .file-upload-input:focus {
      border-color: var(--accent-green);
    }

    .file-upload-input::file-selector-button {
      padding: 8px 16px;
      margin-right: 12px;
      background: var(--accent-green);
      border: none;
      border-radius: 6px;
      color: white;
      font-weight: 500;
      cursor: pointer;
      transition: background 0.2s;
    }

    .file-upload-input::file-selector-button:hover {
      background: #009933;
    }

    .file-upload-label {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 20px;
      background: rgba(0,0,0,0.3);
      border: 2px dashed rgba(255,255,255,0.2);
      border-radius: 8px;
      color: rgba(255,255,255,0.6);
      font-size: 14px;
      cursor: pointer;
      transition: all 0.2s;
    }

    .file-upload-label:hover {
      border-color: var(--accent-green);
      background: rgba(0, 172, 58, 0.1);
      color: var(--accent-green);
    }

    .file-upload-label.has-file {
      border-color: var(--accent-green);
      background: rgba(34, 197, 94, 0.1);
      color: var(--accent-green);
    }

    .file-upload-icon {
      width: 24px;
      height: 24px;
    }

    .file-selected-info {
      margin-top: 8px;
      padding: 8px 12px;
      background: rgba(34, 197, 94, 0.1);
      border-radius: 6px;
      font-size: 12px;
      color: var(--accent-green);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .file-selected-info .file-name {
      font-family: 'JetBrains Mono', monospace;
      word-break: break-all;
    }

    .file-selected-info .file-size {
      color: rgba(255,255,255,0.5);
      margin-left: 8px;
    }

    .file-clear-btn {
      background: transparent;
      border: none;
      color: rgba(255,255,255,0.5);
      cursor: pointer;
      padding: 4px;
      font-size: 16px;
    }

    .file-clear-btn:hover {
      color: var(--accent-red);
    }

    .file-upload-hint {
      margin-top: 8px;
      font-size: 12px;
      color: rgba(255,255,255,0.4);
    }

    .param-input-group {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
    }

    .param-input-label {
      min-width: 120px;
      font-size: 13px;
      color: var(--code-text);
      font-family: 'JetBrains Mono', monospace;
    }

    .param-input-label .required {
      color: var(--accent-red);
      margin-left: 4px;
    }

    .body-field-group {
      margin-bottom: 12px;
      padding: 12px;
      background: rgba(0,0,0,0.2);
      border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.05);
    }

    .body-field-row {
      display: flex;
      align-items: flex-start;
      gap: 12px;
      margin-bottom: 10px;
    }

    .body-field-row:last-child {
      margin-bottom: 0;
    }

    .body-field-label {
      min-width: 140px;
      padding-top: 10px;
      font-size: 13px;
      color: var(--code-text);
      font-family: 'JetBrains Mono', monospace;
    }

    .body-field-label .required {
      color: var(--accent-red);
      margin-left: 4px;
    }

    .body-field-label .field-type {
      display: block;
      font-size: 11px;
      color: rgba(255,255,255,0.4);
      margin-top: 2px;
      font-weight: normal;
    }

    .body-field-input {
      flex: 1;
    }

    .body-field-input input,
    .body-field-input select,
    .body-field-input textarea {
      width: 100%;
      padding: 10px 12px;
      background: rgba(0,0,0,0.3);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 6px;
      color: var(--code-text);
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      outline: none;
      transition: border-color 0.2s;
    }

    .body-field-input input:focus,
    .body-field-input select:focus,
    .body-field-input textarea:focus {
      border-color: var(--accent-green);
    }

    .body-field-input input::placeholder,
    .body-field-input textarea::placeholder {
      color: rgba(255,255,255,0.3);
    }

    .body-field-input textarea {
      min-height: 80px;
      resize: vertical;
    }

    .body-field-input select {
      cursor: pointer;
      appearance: none;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23ffffff' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 12px center;
      padding-right: 36px;
    }

    .body-field-desc {
      font-size: 11px;
      color: rgba(255,255,255,0.4);
      margin-top: 4px;
    }

    .nested-object {
      margin-left: 0;
      margin-top: 8px;
      padding: 12px;
      background: rgba(0,0,0,0.15);
      border-radius: 6px;
      border-left: 2px solid var(--accent-blue);
    }

    .nested-object-label {
      font-size: 11px;
      font-weight: 600;
      color: var(--accent-blue);
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 10px;
    }

    .array-field-container {
      margin-top: 8px;
    }

    .array-item {
      display: flex;
      gap: 8px;
      margin-bottom: 8px;
    }

    .array-item input {
      flex: 1;
    }

    .array-add-btn,
    .array-remove-btn {
      padding: 8px 12px;
      background: rgba(255,255,255,0.1);
      border: 1px solid rgba(255,255,255,0.1);
      border-radius: 6px;
      color: var(--code-text);
      font-size: 12px;
      cursor: pointer;
      transition: all 0.2s;
    }

    .array-add-btn:hover {
      background: rgba(255,255,255,0.15);
    }

    .array-remove-btn {
      padding: 8px 10px;
      background: rgba(239, 68, 68, 0.2);
      border-color: rgba(239, 68, 68, 0.3);
    }

    .array-remove-btn:hover {
      background: rgba(239, 68, 68, 0.3);
    }

    .execute-btn {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      width: 100%;
      padding: 12px;
      background: var(--accent-green);
      color: white;
      border: none;
      border-radius: 8px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }

    .execute-btn:hover {
      background: #009933;
    }

    .execute-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .execute-btn.loading {
      pointer-events: none;
    }

    .response-pre {
      background: rgba(0,0,0,0.3);
      border-radius: 6px;
      padding: 16px;
      max-height: 400px;
      overflow: auto;
      font-size: 12px;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .response-status {
      font-size: 12px;
      padding: 2px 8px;
      border-radius: 4px;
      margin-left: 8px;
    }

    .response-status.success {
      background: rgba(0, 172, 58, 0.12);
      color: #00862d;
    }

    .response-status.error {
      background: rgba(239, 68, 68, 0.12);
      color: #991b1b;
    }

    /* Schema display */
    .schema-block {
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      overflow: hidden;
      margin-top: 8px;
    }

    .schema-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 10px 16px;
      background: var(--bg-tertiary);
      border-bottom: 1px solid var(--border-color);
      cursor: pointer;
    }

    .schema-header:hover {
      background: var(--border-color);
    }

    .schema-name {
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      color: var(--accent-blue);
      font-weight: 500;
    }

    .schema-toggle {
      font-size: 12px;
      color: var(--text-muted);
    }

    .schema-content {
      padding: 16px;
      display: none;
    }

    .schema-block.expanded .schema-content {
      display: block;
    }

    .schema-property {
      display: flex;
      padding: 8px 0;
      border-bottom: 1px solid var(--border-color);
    }

    .schema-property:last-child {
      border-bottom: none;
    }

    .schema-prop-name {
      min-width: 150px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      color: var(--text-primary);
    }

    .schema-prop-type {
      min-width: 100px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: var(--accent-purple);
    }

    .schema-prop-desc {
      flex: 1;
      font-size: 13px;
      color: var(--text-secondary);
    }

    /* Enhanced Schema Table */
    .schema-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }

    .schema-table th {
      text-align: left;
      padding: 10px 12px;
      background: var(--bg-tertiary);
      color: var(--text-secondary);
      font-weight: 500;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      border-bottom: 1px solid var(--border-color);
    }

    .schema-table td {
      padding: 12px;
      border-bottom: 1px solid var(--border-color);
      vertical-align: top;
    }

    .schema-table tr:last-child td {
      border-bottom: none;
    }

    .schema-table tr:hover {
      background: var(--bg-secondary);
    }

    .schema-field-name {
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      color: var(--text-primary);
      font-weight: 500;
    }

    .schema-field-required {
      color: var(--accent-red);
      font-size: 11px;
      font-weight: 500;
      margin-left: 4px;
    }

    .schema-field-type {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: var(--accent-purple);
      background: var(--bg-tertiary);
      padding: 2px 6px;
      border-radius: 4px;
      display: inline-block;
    }

    .schema-field-desc {
      color: var(--text-secondary);
      line-height: 1.5;
    }

    .schema-field-example {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: var(--accent-green);
      background: var(--bg-tertiary);
      padding: 4px 8px;
      border-radius: 4px;
      margin-top: 6px;
      display: inline-block;
      word-break: break-all;
    }

    .schema-field-enum {
      margin-top: 6px;
    }

    .schema-field-enum-value {
      display: inline-block;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      background: var(--bg-tertiary);
      color: var(--accent-orange);
      padding: 2px 6px;
      border-radius: 3px;
      margin: 2px 4px 2px 0;
    }

    .schema-nested {
      margin-left: 20px;
      border-left: 2px solid var(--border-color);
      padding-left: 16px;
      margin-top: 8px;
    }

    .schema-nested-header {
      display: flex;
      align-items: center;
      gap: 8px;
      cursor: pointer;
      padding: 6px 0;
      color: var(--text-secondary);
      font-size: 12px;
    }

    .schema-nested-header:hover {
      color: var(--text-primary);
    }

    .schema-nested-toggle {
      width: 16px;
      height: 16px;
      display: flex;
      align-items: center;
      justify-content: center;
      background: var(--bg-tertiary);
      border-radius: 3px;
      transition: transform 0.15s;
    }

    .schema-nested-toggle.expanded {
      transform: rotate(90deg);
    }

    .schema-nested-content {
      display: none;
      margin-top: 8px;
    }

    .schema-nested-content.expanded {
      display: block;
    }

    .schema-oneof-container {
      margin-top: 8px;
    }

    .schema-oneof-tabs {
      display: flex;
      gap: 4px;
      margin-bottom: 8px;
      flex-wrap: wrap;
    }

    .schema-oneof-tab {
      padding: 6px 12px;
      font-size: 12px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border-color);
      border-radius: 4px;
      cursor: pointer;
      color: var(--text-secondary);
      font-family: 'JetBrains Mono', monospace;
    }

    .schema-oneof-tab:hover {
      background: var(--bg-secondary);
      color: var(--text-primary);
    }

    .schema-oneof-tab.active {
      background: var(--accent-blue);
      color: white;
      border-color: var(--accent-blue);
    }

    .schema-oneof-content {
      display: none;
      border: 1px solid var(--border-color);
      border-radius: 6px;
      padding: 12px;
      background: var(--bg-secondary);
    }

    .schema-oneof-content.active {
      display: block;
    }

    .schema-constraints {
      margin-top: 6px;
      font-size: 11px;
      color: var(--text-muted);
    }

    .schema-constraints span {
      display: inline-block;
      background: var(--bg-tertiary);
      padding: 2px 6px;
      border-radius: 3px;
      margin-right: 6px;
      margin-bottom: 4px;
    }

    .schema-format-badge {
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px;
      background: var(--accent-blue);
      color: white;
      padding: 2px 5px;
      border-radius: 3px;
      margin-left: 6px;
      vertical-align: middle;
    }

    /* Try it button */
    .try-it-btn {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 10px 20px;
      background: var(--accent-green);
      color: white;
      border: none;
      border-radius: 8px;
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.15s;
    }

    .try-it-btn:hover {
      background: #009933;
    }

    /* Loading state */
    .loading {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      color: var(--text-muted);
    }

    .spinner {
      width: 40px;
      height: 40px;
      border: 3px solid var(--border-color);
      border-top-color: var(--accent-green);
      border-radius: 50%;
      animation: spin 1s linear infinite;
    }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    /* Welcome screen */
    .welcome-screen {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      height: 100%;
      text-align: center;
      padding: 40px;
    }

    .welcome-icon {
      width: 80px;
      height: 80px;
      background: #00ac3a;
      border-radius: 20px;
      display: flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 24px;
    }

    .welcome-icon svg {
      width: 50px;
      height: 50px;
    }

    .welcome-title {
      font-family: 'Manrope', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      font-size: 28px;
      font-weight: 700;
      margin-bottom: 12px;
    }

    .welcome-description {
      color: var(--text-secondary);
      font-size: 16px;
      max-width: 500px;
      line-height: 1.7;
    }

    /* Module card */
    .module-list {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 16px;
      margin-top: 24px;
    }

    .module-card {
      padding: 20px;
      background: var(--bg-primary);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      cursor: pointer;
      transition: all 0.2s;
    }

    .module-card:hover {
      border-color: var(--accent-green);
      box-shadow: 0 4px 12px rgba(0, 172, 58, 0.08);
    }

    .module-card-header {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 12px;
    }

    .module-card-icon {
      width: 40px;
      height: 40px;
      background: var(--bg-secondary);
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .module-card-title {
      font-family: 'Manrope', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      font-weight: 600;
      font-size: 16px;
    }

    .module-card-description {
      color: var(--text-secondary);
      font-size: 14px;
      line-height: 1.6;
    }

    .module-card-meta {
      display: flex;
      gap: 16px;
      margin-top: 16px;
      font-size: 12px;
      color: var(--text-muted);
    }

    /* Health Section */
    .health-section {
      margin-top: 32px;
      padding: 24px;
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 12px;
    }

    .health-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 20px;
    }

    .health-title {
      display: flex;
      align-items: center;
      gap: 12px;
      font-family: 'Manrope', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      font-size: 18px;
      font-weight: 600;
    }

    .health-title-icon {
      width: 32px;
      height: 32px;
      background: var(--bg-tertiary);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .health-refresh-btn {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 16px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      color: var(--text-secondary);
      font-size: 13px;
      cursor: pointer;
      transition: all 0.15s;
    }

    .health-refresh-btn:hover {
      background: var(--border-color);
      color: var(--text-primary);
    }

    .health-refresh-btn.loading {
      pointer-events: none;
      opacity: 0.7;
    }

    .health-refresh-btn.loading svg {
      animation: spin 1s linear infinite;
    }

    .health-overall {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 20px;
      padding: 12px 16px;
      background: var(--bg-primary);
      border-radius: 8px;
    }

    .health-overall-label {
      font-size: 13px;
      color: var(--text-muted);
    }

    .health-overall-status {
      font-weight: 600;
      font-size: 14px;
      padding: 4px 12px;
      border-radius: 20px;
    }

    .health-overall-status.healthy {
      background: rgba(0, 172, 58, 0.12);
      color: #00ac3a;
    }

    .health-overall-status.degraded {
      background: rgba(249, 115, 22, 0.12);
      color: #f97316;
    }

    .health-overall-status.unhealthy {
      background: rgba(239, 68, 68, 0.12);
      color: #ef4444;
    }

    [data-theme="dark"] .health-overall-status.healthy {
      background: rgba(0, 172, 58, 0.15);
      color: #00d147;
    }

    [data-theme="dark"] .health-overall-status.degraded {
      background: rgba(249, 115, 22, 0.15);
      color: #fdba74;
    }

    [data-theme="dark"] .health-overall-status.unhealthy {
      background: rgba(239, 68, 68, 0.15);
      color: #fca5a5;
    }

    .health-timestamp {
      margin-left: auto;
      font-size: 12px;
      color: var(--text-muted);
    }

    .health-services {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 12px;
    }

    .health-service-card {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 16px;
      background: var(--bg-primary);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      transition: all 0.15s;
    }

    .health-service-card:hover {
      border-color: var(--accent-green);
    }

    .health-service-indicator {
      width: 12px;
      height: 12px;
      border-radius: 50%;
      flex-shrink: 0;
    }

    .health-service-indicator.healthy {
      background: #00ac3a;
      box-shadow: 0 0 8px rgba(0, 172, 58, 0.4);
    }

    .health-service-indicator.unhealthy {
      background: #ef4444;
      box-shadow: 0 0 8px rgba(239, 68, 68, 0.4);
    }

    .health-service-indicator.unknown {
      background: #6b7280;
    }

    .health-service-info {
      flex: 1;
      min-width: 0;
    }

    .health-service-name {
      font-weight: 500;
      font-size: 14px;
      margin-bottom: 2px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .health-service-details {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      color: var(--text-muted);
    }

    .health-service-port {
      font-family: 'JetBrains Mono', monospace;
      background: var(--bg-tertiary);
      padding: 2px 6px;
      border-radius: 4px;
    }

    .health-service-time {
      color: var(--text-muted);
    }

    .health-service-error {
      font-size: 11px;
      color: #ef4444;
      margin-top: 4px;
    }

    [data-theme="dark"] .health-service-error {
      color: #fca5a5;
    }

    .health-loading {
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 40px;
      color: var(--text-muted);
    }

    .health-loading .spinner {
      width: 24px;
      height: 24px;
      border-width: 2px;
      margin-right: 12px;
    }

    /* Responsive */
    @media (max-width: 1200px) {
      .code-panel {
        display: none;
      }
    }

    @media (max-width: 768px) {
      .sidebar {
        position: fixed;
        left: -280px;
        top: 0;
        bottom: 0;
        z-index: 100;
        transition: left 0.3s;
      }

      .sidebar.open {
        left: 0;
      }
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
    <aside class="sidebar">
      <div class="sidebar-header">
        <a href="/api/v1/docs" class="logo">
          <div class="logo-icon">
            <svg width="24" height="24" viewBox="0 0 614 614" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path fill-rule="evenodd" clip-rule="evenodd" d="M110.72 110.72V186.212L186.217 186.212L186.217 110.72L110.72 110.72Z" fill="#00ac3a"/>
              <path fill-rule="evenodd" clip-rule="evenodd" d="M110.72 427.786V503.277L186.217 503.277L186.217 427.786L110.72 427.786Z" fill="#00ac3a"/>
              <path fill-rule="evenodd" clip-rule="evenodd" d="M427.783 110.72V186.212L503.28 186.212L503.28 110.72L427.783 110.72Z" fill="#00ac3a"/>
              <path fill-rule="evenodd" clip-rule="evenodd" d="M427.779 427.786V503.277L503.275 503.277L503.275 427.786L427.779 427.786Z" fill="#00ac3a"/>
              <path d="M306.998 447.914L362.358 503.277L306.998 558.64L251.637 503.277L306.998 447.914Z" fill="#00ac3a"/>
              <path d="M306.998 55.3602L362.358 110.723L306.998 166.085L251.637 110.723L306.998 55.3602Z" fill="#00ac3a"/>
              <path d="M306.998 251.642L362.358 307.005L306.998 362.367L251.637 307.005L306.998 251.642Z" fill="#00ac3a"/>
              <path d="M503.275 251.637L558.635 307L503.275 362.363L447.914 307L503.275 251.637Z" fill="#00ac3a"/>
              <path d="M110.72 251.637L166.081 307L110.72 362.363L55.3602 307L110.72 251.637Z" fill="#00ac3a"/>
            </svg>
          </div>
          <span>PipesHub API</span>
        </a>
        <div class="search-box">
          <svg class="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"></circle>
            <path d="M21 21l-4.35-4.35"></path>
          </svg>
          <input type="text" class="search-input" placeholder="Search endpoints..." id="searchInput">
        </div>
      </div>
      <nav class="sidebar-nav" id="sidebarNav">
        <div class="loading">
          <div class="spinner"></div>
        </div>
      </nav>
    </aside>

    <div class="resize-handle resize-handle-right" id="sidebarResizeHandle"></div>

    <main class="main-content">
      <header class="content-header">
        <div class="breadcrumb" id="breadcrumb">
          <span>API Reference</span>
        </div>
        <div style="display: flex; align-items: center; gap: 12px;">
          <button class="theme-toggle" id="themeToggle" title="Toggle dark mode">
            <svg class="moon-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
            </svg>
            <svg class="sun-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="5"></circle>
              <line x1="12" y1="1" x2="12" y2="3"></line>
              <line x1="12" y1="21" x2="12" y2="23"></line>
              <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
              <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
              <line x1="1" y1="12" x2="3" y2="12"></line>
              <line x1="21" y1="12" x2="23" y2="12"></line>
              <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
              <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
            </svg>
          </button>
          <button class="try-it-btn" id="tryItBtn" style="display: none;">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polygon points="5 3 19 12 5 21 5 3"></polygon>
          </svg>
          Try It
        </button>
        </div>
      </header>

      <div class="content-body">
        <div class="doc-panel" id="docPanel">
          <div class="welcome-screen">
            <div class="welcome-icon">
              <svg viewBox="0 0 614 614" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path fill-rule="evenodd" clip-rule="evenodd" d="M110.72 110.72V186.212L186.217 186.212L186.217 110.72L110.72 110.72Z" fill="white"/>
                <path fill-rule="evenodd" clip-rule="evenodd" d="M110.72 427.786V503.277L186.217 503.277L186.217 427.786L110.72 427.786Z" fill="white"/>
                <path fill-rule="evenodd" clip-rule="evenodd" d="M427.783 110.72V186.212L503.28 186.212L503.28 110.72L427.783 110.72Z" fill="white"/>
                <path fill-rule="evenodd" clip-rule="evenodd" d="M427.779 427.786V503.277L503.275 503.277L503.275 427.786L427.779 427.786Z" fill="white"/>
                <path d="M306.998 447.914L362.358 503.277L306.998 558.64L251.637 503.277L306.998 447.914Z" fill="white"/>
                <path d="M306.998 55.3602L362.358 110.723L306.998 166.085L251.637 110.723L306.998 55.3602Z" fill="white"/>
                <path d="M306.998 251.642L362.358 307.005L306.998 362.367L251.637 307.005L306.998 251.642Z" fill="white"/>
                <path d="M503.275 251.637L558.635 307L503.275 362.363L447.914 307L503.275 251.637Z" fill="white"/>
                <path d="M110.72 251.637L166.081 307L110.72 362.363L55.3602 307L110.72 251.637Z" fill="white"/>
              </svg>
            </div>
            <h1 class="welcome-title">Welcome to PipesHub API</h1>
            <p class="welcome-description">
              Explore our comprehensive API documentation. Select a module from the sidebar
              to view available endpoints and learn how to integrate with PipesHub.
            </p>
          </div>

          <!-- Health Section - Initial Loading State -->
          <div class="health-section" id="healthSection">
            <div class="health-header">
              <div class="health-title">
                <div class="health-title-icon">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M22 12h-4l-3 9L9 3l-3 9H2"></path>
                  </svg>
                </div>
                <span>Service Health</span>
              </div>
            </div>
            <div class="health-loading">
              <div class="spinner"></div>
              <span>Checking service health...</span>
            </div>
          </div>
        </div>

        <div class="resize-handle resize-handle-left" id="codePanelResizeHandle" style="display: none;"></div>

        <div class="code-panel" id="codePanel" style="display: none;">
          <div class="code-panel-header">
            <div class="code-tab active" data-tab="tryit">Try It</div>
            <div class="code-tab" data-tab="curl">cURL</div>
            <div class="code-tab" data-tab="javascript">JavaScript</div>
            <div class="code-tab" data-tab="python">Python</div>
          </div>
          <div class="code-content" id="codeContent">
            <!-- Try It Panel -->
            <div id="tryItPanel" class="try-it-panel">
              <div class="try-it-section">
                <label class="try-it-label">Base URL</label>
                <input type="text" id="baseUrlInput" class="try-it-input" placeholder="https://api.example.com">
              </div>
              <div class="try-it-section">
                <label class="try-it-label">Authorization Token</label>
                <input type="text" id="authTokenInput" class="try-it-input" placeholder="Bearer your-token-here">
              </div>
              <div id="headersSection" class="try-it-section" style="display: none;">
                <label class="try-it-label">Headers</label>
                <div id="headersInputs"></div>
              </div>
              <div id="pathParamsSection" class="try-it-section" style="display: none;">
                <label class="try-it-label">Path Parameters</label>
                <div id="pathParamsInputs"></div>
              </div>
              <div id="queryParamsSection" class="try-it-section" style="display: none;">
                <label class="try-it-label">Query Parameters</label>
                <div id="queryParamsInputs"></div>
              </div>
              <div id="requestBodySection" class="try-it-section" style="display: none;">
                <label class="try-it-label">Request Body</label>
                <div id="requestBodyInputs"></div>
              </div>
              <button id="executeBtn" class="execute-btn">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polygon points="5 3 19 12 5 21 5 3"></polygon>
                </svg>
                Execute
              </button>
              <div id="responseSection" class="try-it-section" style="display: none;">
                <label class="try-it-label">Response <span id="responseStatus"></span></label>
                <pre id="responseBody" class="response-pre"></pre>
              </div>
            </div>
            <!-- Code Examples Panel -->
            <div id="codeExamplesPanel" style="display: none;"></div>
          </div>
        </div>
      </div>
    </main>
  </div>

  <script>
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

      // Load initial health status
      loadInitialHealthStatus();
    });

    // Load health status on page load
    async function loadInitialHealthStatus() {
      try {
        const response = await fetch('/api/v1/docs/health');
        if (!response.ok) throw new Error('Health check failed');
        const healthData = await response.json();

        const healthSection = document.getElementById('healthSection');
        if (healthSection) {
          healthSection.outerHTML = renderHealthSection(healthData);
        }
      } catch (error) {
        console.error('Failed to load initial health status:', error);
        // Keep the loading state or show error
        const healthSection = document.getElementById('healthSection');
        if (healthSection) {
          healthSection.innerHTML =
            '<div class="health-header">' +
            '<div class="health-title">' +
            '<div class="health-title-icon">' +
            '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
            '<path d="M22 12h-4l-3 9L9 3l-3 9H2"></path>' +
            '</svg>' +
            '</div>' +
            '<span>Service Health</span>' +
            '</div>' +
            '<button class="health-refresh-btn" onclick="refreshHealth()">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
            '<path d="M23 4v6h-6"></path>' +
            '<path d="M1 20v-6h6"></path>' +
            '<path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>' +
            '</svg>' +
            '<span>Retry</span>' +
            '</button>' +
            '</div>' +
            '<div class="health-overall">' +
            '<span class="health-overall-label">Status:</span>' +
            '<span class="health-overall-status unhealthy">Unable to check</span>' +
            '</div>';
        }
      }
    }

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
        renderSidebar();
      } catch (error) {
        console.error('Failed to load API docs:', error);
        document.getElementById('sidebarNav').innerHTML =
          '<div class="loading" style="color: var(--accent-red);">Failed to load documentation</div>';
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
        return '<div class="code-block" style="background: var(--bg-secondary);"><pre style="color: var(--text-primary);">' + escapeHtml(JSON.stringify(schema, null, 2)) + '</pre></div>';
      }

      let html = '<div class="schema-block">';

      // Schema header
      const schemaName = name || (schema.$ref ? schema.$ref.replace('#/components/schemas/', '') : 'Schema');
      html += '<div class="schema-header">';
      html += '<span class="schema-name">' + escapeHtml(schemaName) + '</span>';
      html += '<span class="schema-toggle">Click to expand</span>';
      html += '</div>';

      html += '<div class="schema-content">';

      // Render as detailed table
      html += renderSchemaTable(resolved, [], 0);

      html += '</div></div>';
      return html;
    }

    // Render schema as detailed table
    function renderSchemaTable(schema, requiredFields, depth) {
      if (depth > 6) return '<div style="color: var(--text-muted); font-size: 12px;">...</div>';

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
        html += '<div style="color: var(--text-muted); font-size: 12px; margin-bottom: 8px;">Array items:</div>';
        html += renderSchemaTable(resolved.items, [], depth + 1);
        html += '</td></tr>';
      } else {
        html += '<tr><td colspan="3" style="color: var(--text-muted);">';
        html += '<span class="schema-field-type">' + escapeHtml(resolved.type || 'any') + '</span>';
        if (resolved.description) {
          html += '<div style="margin-top: 6px;">' + escapeHtml(resolved.description) + '</div>';
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
        html += escapeHtml(resolved.description);
      }

      // Enum values
      if (resolved.enum) {
        html += '<div class="schema-field-enum">Allowed values: ';
        for (const val of resolved.enum) {
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

      if (constraints.length > 0) {
        html += '<div class="schema-constraints">';
        for (const c of constraints) {
          html += '<span>' + escapeHtml(c) + '</span>';
        }
        html += '</div>';
      }

      // Example value
      if (resolved.example !== undefined) {
        const exampleStr = typeof resolved.example === 'object'
          ? JSON.stringify(resolved.example)
          : String(resolved.example);
        html += '<div class="schema-field-example">Example: ' + escapeHtml(exampleStr) + '</div>';
      } else if (resolved.default !== undefined) {
        const defaultStr = typeof resolved.default === 'object'
          ? JSON.stringify(resolved.default)
          : String(resolved.default);
        html += '<div class="schema-field-example">Default: ' + escapeHtml(defaultStr) + '</div>';
      }

      // Handle oneOf/anyOf
      if (resolved.oneOf || resolved.anyOf) {
        const variants = resolved.oneOf || resolved.anyOf;
        const variantType = resolved.oneOf ? 'oneOf' : 'anyOf';
        const variantId = 'variant_' + Math.random().toString(36).substr(2, 9);

        html += '<div class="schema-oneof-container">';
        html += '<div style="color: var(--text-muted); font-size: 11px; margin-bottom: 6px;">' + variantType + ' - click to see options:</div>';
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
            } else if (variantResolved.type === 'string') {
              html += '<div style="padding: 8px; color: var(--text-secondary);">';
              html += '<span class="schema-field-type">string</span>';
              if (variantResolved.description) {
                html += '<div style="margin-top: 6px;">' + escapeHtml(variantResolved.description) + '</div>';
              }
              html += '</div>';
            } else {
              html += '<div style="padding: 8px;"><span class="schema-field-type">' + escapeHtml(variantResolved.type || 'object') + '</span></div>';
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
        html += '<span class="schema-nested-toggle">▶</span>';
        html += '<span>Show ' + Object.keys(resolved.properties).length + ' properties</span>';
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
          html += '<span class="schema-nested-toggle">▶</span>';
          html += '<span>Show array item schema</span>';
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
      if (depth > 5) return '...';

      const resolved = resolveSchema(schema);
      if (!resolved) return {};

      if (resolved.example !== undefined) return resolved.example;
      if (resolved.default !== undefined) return resolved.default;

      switch (resolved.type) {
        case 'string':
          if (resolved.enum) return resolved.enum[0];
          if (resolved.format === 'date-time') return new Date().toISOString();
          if (resolved.format === 'date') return new Date().toISOString().split('T')[0];
          if (resolved.format === 'email') return 'user@example.com';
          if (resolved.format === 'uri') return 'https://example.com';
          return 'string';
        case 'number':
        case 'integer':
          return resolved.minimum || 0;
        case 'boolean':
          return true;
        case 'array':
          return [generateExample(resolved.items || {}, depth + 1)];
        case 'object':
          if (!resolved.properties) return {};
          const obj = {};
          for (const [key, value] of Object.entries(resolved.properties)) {
            obj[key] = generateExample(value, depth + 1);
          }
          return obj;
        default:
          if (resolved.properties) {
            const obj = {};
            for (const [key, value] of Object.entries(resolved.properties)) {
              obj[key] = generateExample(value, depth + 1);
            }
            return obj;
          }
          return null;
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

      nav.innerHTML = html;
    }

    // Render a single module nav item with its endpoints
    function renderModuleNavItem(module) {
      let html = '';
      html += '<a class="nav-item" data-module="' + module.id + '">';
      html += '  <span class="nav-item-icon">';
      html += getModuleIcon(module.id);
      html += '  </span>';
      html += '  <span>' + escapeHtml(module.name) + '</span>';
      html += '</a>';

      // Group endpoints by tag
      const moduleEndpoints = apiDocs.endpoints.filter(e => e.moduleId === module.id);
      if (moduleEndpoints.length > 0) {
        // Check if this is an internal service module
        const internalServiceIds = ['query-service', 'indexing-service', 'connector-service-internal', 'docling-service'];
        const isInternalService = internalServiceIds.includes(module.id);

        // Group by tag
        const byTag = {};
        for (const ep of moduleEndpoints) {
          const tag = ep.tags[0] || 'Other';
          if (!byTag[tag]) byTag[tag] = [];
          byTag[tag].push(ep);
        }

        html += '<div class="endpoint-list" data-module="' + module.id + '" style="display: none;">';

        // For internal services, render endpoints directly without tag headers
        if (isInternalService) {
          html += '<div class="tag-section" data-module="' + module.id + '">';
          html += '<div class="tag-endpoints" style="display: block;">';
          for (const endpoint of moduleEndpoints) {
            html += '<div class="endpoint-item" data-endpoint="' + endpoint.operationId + '">';
            html += '  <span class="method-badge method-' + endpoint.method.toLowerCase() + '">' + endpoint.method + '</span>';
            html += '  <span>' + escapeHtml(endpoint.summary || endpoint.path) + '</span>';
            html += '</div>';
          }
          html += '</div>';
          html += '</div>';
        } else {
          // Render each tag as a subsection for non-internal services
          for (const [tag, endpoints] of Object.entries(byTag)) {
            html += '<div class="tag-section" data-module="' + module.id + '" data-tag="' + escapeHtml(tag) + '">';
            html += '<div class="tag-header">';
            html += '<svg class="tag-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>';
            html += '<span class="tag-name">' + escapeHtml(tag) + '</span>';
            html += '<span class="tag-count">' + endpoints.length + '</span>';
            html += '</div>';

            html += '<div class="tag-endpoints">';
            for (const endpoint of endpoints) {
              html += '<div class="endpoint-item" data-endpoint="' + endpoint.operationId + '">';
              html += '  <span class="method-badge method-' + endpoint.method.toLowerCase() + '">' + endpoint.method + '</span>';
              html += '  <span>' + escapeHtml(endpoint.summary || endpoint.path) + '</span>';
              html += '</div>';
            }
            html += '</div>';
            html += '</div>';
          }
        }

        html += '</div>';
      }

      return html;
    }

    // Get module icon SVG
    function getModuleIcon(moduleId) {
      const icons = {
        'auth': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0110 0v4"></path></svg>',
        'user-management': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>',
        'storage': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"></path></svg>',
        'knowledge-base': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 016.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"></path></svg>',
        'enterprise-search': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><path d="M21 21l-4.35-4.35"></path></svg>',
        'connector-manager': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"></polyline><polyline points="8 6 2 12 8 18"></polyline></svg>',
        'configuration-manager': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z"></path></svg>',
        'crawling-manager': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>',
        'mail': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"></path><polyline points="22,6 12,13 2,6"></polyline></svg>',
        'query-service': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><path d="M21 21l-4.35-4.35"></path><path d="M11 8v6"></path><path d="M8 11h6"></path></svg>',
        'indexing-service': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line></svg>',
        'connector-service-internal': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"></path><path d="M2 17l10 5 10-5"></path><path d="M2 12l10 5 10-5"></path></svg>',
        'docling-service': '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.5 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V7.5L14.5 2z"></path><polyline points="14 2 14 8 20 8"></polyline><path d="M12 18v-6"></path><path d="M9 15h6"></path></svg>',
      };
      return icons[moduleId] || '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle></svg>';
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
          selectEndpoint(operationId);
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

        const responseHeader = e.target.closest('.response-header');
        if (responseHeader && responseHeader.dataset.endpoint) {
          selectEndpoint(responseHeader.dataset.endpoint);
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

      // Theme toggle
      document.getElementById('themeToggle')?.addEventListener('click', toggleTheme);
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

    // Execute API request
    async function executeRequest() {
      if (!currentEndpoint) return;

      const ep = currentEndpoint;
      const module = apiDocs.modules.find(m => m.id === ep.moduleId);
      const baseUrl = document.getElementById('baseUrlInput').value || config.baseUrl;
      const authToken = document.getElementById('authTokenInput').value || config.authToken;

      // Build URL with path params
      let url = baseUrl + module.basePath + ep.path;

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
          const body = collectBodyFields();
          if (Object.keys(body).length > 0) {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(body);
          }
        }
      }

      // Show loading state
      const executeBtn = document.getElementById('executeBtn');
      const originalText = executeBtn.innerHTML;
      executeBtn.innerHTML = '<span>Executing...</span>';
      executeBtn.disabled = true;

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
      } catch (error) {
        responseSection.style.display = 'block';
        responseStatus.textContent = 'Error';
        responseStatus.className = 'response-status error';
        responseBody.textContent = error.message;
      } finally {
        executeBtn.innerHTML = originalText;
        executeBtn.disabled = false;
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
      const module = apiDocs.modules.find(m => m.id === ep.moduleId);

      // Set default base URL
      const baseUrlInput = document.getElementById('baseUrlInput');
      if (!baseUrlInput.value) {
        baseUrlInput.value = window.location.origin;
      }

      // Header parameters
      const headerParams = ep.parameters?.filter(p => p.in === 'header') || [];
      const headersSection = document.getElementById('headersSection');
      const headersInputs = document.getElementById('headersInputs');

      if (headerParams.length > 0) {
        headersSection.style.display = 'block';
        headersInputs.innerHTML = headerParams.map(param =>
          '<div class="param-input-group">' +
          '<span class="param-input-label">' + escapeHtml(param.name) +
          (param.required ? '<span class="required">*</span>' : '') +
          '</span>' +
          '<input type="text" class="try-it-input" data-header="' + escapeHtml(param.name) + '" ' +
          'placeholder="' + escapeHtml(param.description || param.name) + '">' +
          '</div>'
        ).join('');
      } else {
        headersSection.style.display = 'none';
      }

      // Path parameters
      const pathParams = ep.parameters?.filter(p => p.in === 'path') || [];
      const pathParamsSection = document.getElementById('pathParamsSection');
      const pathParamsInputs = document.getElementById('pathParamsInputs');

      if (pathParams.length > 0) {
        pathParamsSection.style.display = 'block';
        pathParamsInputs.innerHTML = pathParams.map(param =>
          '<div class="param-input-group">' +
          '<span class="param-input-label">' + escapeHtml(param.name) +
          (param.required ? '<span class="required">*</span>' : '') +
          '</span>' +
          '<input type="text" class="try-it-input" data-param="' + escapeHtml(param.name) + '" ' +
          'placeholder="' + escapeHtml(param.description || param.name) + '">' +
          '</div>'
        ).join('');
      } else {
        pathParamsSection.style.display = 'none';
      }

      // Query parameters
      const queryParams = ep.parameters?.filter(p => p.in === 'query') || [];
      const queryParamsSection = document.getElementById('queryParamsSection');
      const queryParamsInputs = document.getElementById('queryParamsInputs');

      if (queryParams.length > 0) {
        queryParamsSection.style.display = 'block';
        queryParamsInputs.innerHTML = queryParams.map(param =>
          '<div class="param-input-group">' +
          '<span class="param-input-label">' + escapeHtml(param.name) +
          (param.required ? '<span class="required">*</span>' : '') +
          '</span>' +
          '<input type="text" class="try-it-input" data-param="' + escapeHtml(param.name) + '" ' +
          'placeholder="' + escapeHtml(param.description || param.name) + '">' +
          '</div>'
        ).join('');
      } else {
        queryParamsSection.style.display = 'none';
      }

      // Request body
      const requestBodySection = document.getElementById('requestBodySection');
      const requestBodyInputs = document.getElementById('requestBodyInputs');

      if (ep.requestBody && ['POST', 'PUT', 'PATCH'].includes(ep.method)) {
        requestBodySection.style.display = 'block';

        // Check for multipart/form-data (file upload)
        const multipartContent = ep.requestBody.content?.['multipart/form-data'];
        const jsonContent = ep.requestBody.content?.['application/json'];

        if (multipartContent?.schema) {
          // Handle file upload
          const resolved = resolveSchema(multipartContent.schema);
          requestBodyInputs.innerHTML = renderMultipartFields(resolved, multipartContent.schema.required || resolved?.required || []);
        } else if (jsonContent?.schema) {
          const resolved = resolveSchema(jsonContent.schema);
          requestBodyInputs.innerHTML = renderBodyFields(resolved, jsonContent.schema.required || resolved?.required || []);
        } else {
          requestBodyInputs.innerHTML = '<div class="body-field-desc">No schema available</div>';
        }
      } else {
        requestBodySection.style.display = 'none';
      }

      // Reset response
      document.getElementById('responseSection').style.display = 'none';
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
            // Array field
            const itemType = propResolved.items ? getFieldType(resolveSchema(propResolved.items)) : 'any';
            html += '<div class="array-field-container" data-array-field="' + escapeHtml(fieldPath) + '">';
            html += '<div class="array-item">';
            html += '<input type="text" class="try-it-input" placeholder="' + escapeHtml(itemType) + ' item" data-array-item="' + escapeHtml(fieldPath) + '">';
            html += '<button type="button" class="array-remove-btn" onclick="removeArrayItem(this)">✕</button>';
            html += '</div>';
            html += '<button type="button" class="array-add-btn" onclick="addArrayItem(\\'' + escapeHtml(fieldPath) + '\\', \\'' + escapeHtml(itemType) + '\\')">+ Add item</button>';
            html += '</div>';
          } else if (propResolved?.type === 'integer' || propResolved?.type === 'number') {
            // Number field
            html += '<input type="number" data-body-field="' + escapeHtml(fieldPath) + '" ';
            html += 'placeholder="' + escapeHtml(propResolved.description || propName) + '"';
            if (defaultValue !== undefined && defaultValue !== null) html += ' value="' + defaultValue + '"';
            html += '>';
          } else {
            // String or other field - render as input or textarea
            const isLongText = propResolved?.maxLength > 200 || propResolved?.format === 'text';
            if (isLongText) {
              html += '<textarea data-body-field="' + escapeHtml(fieldPath) + '" ';
              html += 'placeholder="' + escapeHtml(propResolved?.description || propName) + '">';
              html += defaultValue !== undefined ? escapeHtml(String(defaultValue)) : '';
              html += '</textarea>';
            } else {
              html += '<input type="text" data-body-field="' + escapeHtml(fieldPath) + '" ';
              html += 'placeholder="' + escapeHtml(propResolved?.description || propName) + '"';
              if (defaultValue !== undefined && defaultValue !== null) html += ' value="' + escapeHtml(String(defaultValue)) + '"';
              html += '>';
            }
          }

          if (propResolved?.description) {
            html += '<div class="body-field-desc">' + escapeHtml(propResolved.description) + '</div>';
          }

          html += '</div>';
          html += '</div>';
        }
      }

      html += '</div>';
      return html;
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
            html += '<button type="button" class="file-clear-btn" onclick="clearFileSelection(\\'' + escapeHtml(propName) + '\\')">&times;</button>';
            html += '</div>';
            html += '<div class="file-upload-hint">Maximum file size: 100MB</div>';
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
            html += '<input type="text" data-body-field="' + escapeHtml(propName) + '" placeholder="' + escapeHtml(propResolved?.description || propName) + '">';
          }

          if (propResolved?.description) {
            html += '<div class="body-field-desc">' + escapeHtml(propResolved.description) + '</div>';
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

    // Add array item
    function addArrayItem(fieldPath, itemType) {
      const container = document.querySelector('[data-array-field="' + fieldPath + '"]');
      if (!container) return;

      const newItem = document.createElement('div');
      newItem.className = 'array-item';
      newItem.innerHTML = '<input type="text" class="try-it-input" placeholder="' + escapeHtml(itemType) + ' item" data-array-item="' + escapeHtml(fieldPath) + '">' +
        '<button type="button" class="array-remove-btn" onclick="removeArrayItem(this)">✕</button>';

      const addBtn = container.querySelector('.array-add-btn');
      container.insertBefore(newItem, addBtn);
    }

    // Remove array item
    function removeArrayItem(btn) {
      const item = btn.closest('.array-item');
      const container = item.closest('.array-field-container');

      // Keep at least one item
      if (container.querySelectorAll('.array-item').length > 1) {
        item.remove();
      }
    }

    // Make functions globally accessible
    window.addArrayItem = addArrayItem;
    window.removeArrayItem = removeArrayItem;

    // Fetch and render health status
    async function fetchHealthStatus() {
      try {
        const response = await fetch('/api/v1/docs/health');
        if (!response.ok) throw new Error('Health check failed');
        return await response.json();
      } catch (error) {
        console.error('Failed to fetch health status:', error);
        return null;
      }
    }

    function renderHealthSection(healthData) {
      if (!healthData) {
        return '<div class="health-section">' +
          '<div class="health-header">' +
          '<div class="health-title">' +
          '<div class="health-title-icon">' +
          '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
          '<path d="M22 12h-4l-3 9L9 3l-3 9H2"></path>' +
          '</svg>' +
          '</div>' +
          '<span>Service Health</span>' +
          '</div>' +
          '</div>' +
          '<div class="health-loading">' +
          '<div class="spinner"></div>' +
          '<span>Checking service health...</span>' +
          '</div>' +
          '</div>';
      }

      const timestamp = new Date(healthData.timestamp).toLocaleTimeString();
      let html = '<div class="health-section">' +
        '<div class="health-header">' +
        '<div class="health-title">' +
        '<div class="health-title-icon">' +
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
        '<path d="M22 12h-4l-3 9L9 3l-3 9H2"></path>' +
        '</svg>' +
        '</div>' +
        '<span>Service Health</span>' +
        '</div>' +
        '<button class="health-refresh-btn" onclick="refreshHealth()">' +
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
        '<path d="M23 4v6h-6"></path>' +
        '<path d="M1 20v-6h6"></path>' +
        '<path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>' +
        '</svg>' +
        '<span>Refresh</span>' +
        '</button>' +
        '</div>';

      html += '<div class="health-overall">' +
        '<span class="health-overall-label">Overall Status:</span>' +
        '<span class="health-overall-status ' + healthData.overallStatus + '">' +
        healthData.overallStatus.charAt(0).toUpperCase() + healthData.overallStatus.slice(1) +
        '</span>' +
        '<span class="health-timestamp">Last checked: ' + timestamp + '</span>' +
        '</div>';

      html += '<div class="health-services">';
      for (const service of healthData.services) {
        html += '<div class="health-service-card">' +
          '<div class="health-service-indicator ' + service.status + '"></div>' +
          '<div class="health-service-info">' +
          '<div class="health-service-name">' + escapeHtml(service.name) + '</div>' +
          '<div class="health-service-details">' +
          '<span class="health-service-port">:' + service.port + '</span>';

        if (service.responseTime !== undefined && service.status === 'healthy') {
          html += '<span class="health-service-time">' + service.responseTime + 'ms</span>';
        }
        html += '</div>';

        if (service.error) {
          html += '<div class="health-service-error">' + escapeHtml(service.error) + '</div>';
        }
        html += '</div></div>';
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
      document.getElementById('codePanel').style.display = 'none';
      document.getElementById('codePanelResizeHandle').style.display = 'none';
      document.getElementById('tryItBtn').style.display = 'none';

      document.getElementById('docPanel').innerHTML =
        '<div class="welcome-screen">' +
        '<div class="welcome-icon">' +
        '<svg viewBox="0 0 614 614" fill="none" xmlns="http://www.w3.org/2000/svg">' +
        '<path fill-rule="evenodd" clip-rule="evenodd" d="M110.72 110.72V186.212L186.217 186.212L186.217 110.72L110.72 110.72Z" fill="white"/>' +
        '<path fill-rule="evenodd" clip-rule="evenodd" d="M110.72 427.786V503.277L186.217 503.277L186.217 427.786L110.72 427.786Z" fill="white"/>' +
        '<path fill-rule="evenodd" clip-rule="evenodd" d="M427.783 110.72V186.212L503.28 186.212L503.28 110.72L427.783 110.72Z" fill="white"/>' +
        '<path fill-rule="evenodd" clip-rule="evenodd" d="M427.779 427.786V503.277L503.275 503.277L503.275 427.786L427.779 427.786Z" fill="white"/>' +
        '<path d="M306.998 447.914L362.358 503.277L306.998 558.64L251.637 503.277L306.998 447.914Z" fill="white"/>' +
        '<path d="M306.998 55.3602L362.358 110.723L306.998 166.085L251.637 110.723L306.998 55.3602Z" fill="white"/>' +
        '<path d="M306.998 251.642L362.358 307.005L306.998 362.367L251.637 307.005L306.998 251.642Z" fill="white"/>' +
        '<path d="M503.275 251.637L558.635 307L503.275 362.363L447.914 307L503.275 251.637Z" fill="white"/>' +
        '<path d="M110.72 251.637L166.081 307L110.72 362.363L55.3602 307L110.72 251.637Z" fill="white"/>' +
        '</svg>' +
        '</div>' +
        '<h1 class="welcome-title">Welcome to PipesHub API</h1>' +
        '<p class="welcome-description">' +
        'Explore our comprehensive API documentation. Select a module from the sidebar ' +
        'to view available endpoints and learn how to integrate with PipesHub.' +
        '</p>' +
        '</div>' +
        renderHealthSection(null);

      // Fetch and update health status asynchronously
      fetchHealthStatus().then(healthData => {
        const healthSection = document.querySelector('.health-section');
        if (healthSection) {
          healthSection.outerHTML = renderHealthSection(healthData);
        }
      });
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
      const endpoints = apiDocs.endpoints.filter(e => e.moduleId === moduleId);

      document.getElementById('breadcrumb').innerHTML =
        '<span class="breadcrumb-link" data-action="home">API Reference</span>' +
        '<span class="breadcrumb-separator">/</span>' +
        '<span class="breadcrumb-current">' + escapeHtml(module.name) + '</span>';

      document.getElementById('codePanel').style.display = 'none';
      document.getElementById('codePanelResizeHandle').style.display = 'none';
      document.getElementById('tryItBtn').style.display = 'none';

      let html = '<div class="endpoint-header">';
      html += '<div class="endpoint-title">';
      html += '<h1>' + escapeHtml(module.name) + '</h1>';
      html += '</div>';
      html += '<div class="endpoint-description">' + renderDescription(module.description) + '</div>';
      html += '</div>';

      html += '<div class="doc-section">';
      html += '<h2 class="section-title">Endpoints</h2>';
      html += '<div class="module-list">';

      // Group endpoints by tag
      const byTag = {};
      for (const ep of endpoints) {
        const tag = ep.tags[0] || 'Other';
        if (!byTag[tag]) byTag[tag] = [];
        byTag[tag].push(ep);
      }

      for (const [tag, tagEndpoints] of Object.entries(byTag)) {
        html += '<div class="module-card" data-module-id="' + escapeHtml(moduleId) + '" data-tag="' + escapeHtml(tag) + '">';
        html += '<div class="module-card-header">';
        html += '<div class="module-card-icon">' + getModuleIcon(moduleId) + '</div>';
        html += '<div class="module-card-title">' + escapeHtml(tag) + '</div>';
        html += '</div>';
        html += '<div class="module-card-meta">';
        html += '<span>' + tagEndpoints.length + ' endpoints</span>';
        html += '</div>';
        html += '</div>';
      }

      html += '</div>';
      html += '</div>';

      document.getElementById('docPanel').innerHTML = html;
    }

    // Show tag endpoints
    function showTagEndpoints(moduleId, tag) {
      const module = apiDocs.modules.find(m => m.id === moduleId);
      const endpoints = apiDocs.endpoints.filter(e => e.moduleId === moduleId && (e.tags[0] || 'Other') === tag);

      document.getElementById('breadcrumb').innerHTML =
        '<span class="breadcrumb-link" data-action="home">API Reference</span>' +
        '<span class="breadcrumb-separator">/</span>' +
        '<span class="breadcrumb-link" data-action="module" data-module-id="' + escapeHtml(moduleId) + '">' + escapeHtml(module.name) + '</span>' +
        '<span class="breadcrumb-separator">/</span>' +
        '<span class="breadcrumb-current">' + escapeHtml(tag) + '</span>';

      document.getElementById('codePanel').style.display = 'none';
      document.getElementById('codePanelResizeHandle').style.display = 'none';
      document.getElementById('tryItBtn').style.display = 'none';

      let html = '<div class="endpoint-header">';
      html += '<div class="endpoint-title">';
      html += '<h1>' + escapeHtml(tag) + '</h1>';
      html += '</div>';
      html += '</div>';

      html += '<div class="doc-section">';
      for (const ep of endpoints) {
        html += '<div class="response-item">';
        html += '<div class="response-header" data-endpoint="' + escapeHtml(ep.operationId) + '" style="cursor: pointer;">';
        html += '<span class="method-badge method-' + ep.method.toLowerCase() + '">' + ep.method + '</span>';
        html += '<span style="flex: 1; margin-left: 12px;"><strong>' + escapeHtml(ep.summary || 'Untitled') + '</strong></span>';
        html += '<span style="font-family: monospace; font-size: 12px; color: var(--text-muted);">' + escapeHtml(ep.path) + '</span>';
        html += '</div>';
        html += '</div>';
      }
      html += '</div>';

      document.getElementById('docPanel').innerHTML = html;
    }

    // Select endpoint
    function selectEndpoint(operationId) {
      currentEndpoint = apiDocs.endpoints.find(e => e.operationId === operationId);
      if (!currentEndpoint) return;

      const module = apiDocs.modules.find(m => m.id === currentEndpoint.moduleId);

      // Update active state
      document.querySelectorAll('.endpoint-item').forEach(el => {
        el.classList.toggle('active', el.dataset.endpoint === operationId);
      });

      const tag = currentEndpoint.tags[0] || 'Other';
      document.getElementById('breadcrumb').innerHTML =
        '<span class="breadcrumb-link" data-action="home">API Reference</span>' +
        '<span class="breadcrumb-separator">/</span>' +
        '<span class="breadcrumb-link" data-action="module" data-module-id="' + escapeHtml(module.id) + '">' + escapeHtml(module.name) + '</span>' +
        '<span class="breadcrumb-separator">/</span>' +
        '<span class="breadcrumb-link" data-action="tag" data-module-id="' + escapeHtml(module.id) + '" data-tag="' + escapeHtml(tag) + '">' + escapeHtml(tag) + '</span>' +
        '<span class="breadcrumb-separator">/</span>' +
        '<span class="breadcrumb-current">' + escapeHtml(currentEndpoint.summary || currentEndpoint.operationId) + '</span>';

      document.getElementById('codePanel').style.display = 'flex';
      document.getElementById('codePanelResizeHandle').style.display = 'block';
      document.getElementById('tryItBtn').style.display = 'none'; // Hide Try It button, we have it in panel

      showEndpointDetails();
      setupTryItPanel();
      switchTab(currentTab);
    }

    // Show endpoint details
    function showEndpointDetails() {
      const ep = currentEndpoint;

      let html = '<div class="endpoint-header">';
      html += '<div class="endpoint-title">';
      html += '<span class="method-badge method-' + ep.method.toLowerCase() + '" style="font-size: 14px; padding: 6px 12px;">' + ep.method + '</span>';
      html += '<h1>' + escapeHtml(ep.summary || ep.operationId) + '</h1>';
      html += '</div>';
      html += '<div class="endpoint-path">' + escapeHtml(ep.path) + '</div>';
      if (ep.description) {
        html += '<div class="endpoint-description">' + renderDescription(ep.description) + '</div>';
      }
      html += '</div>';

      // Parameters
      if (ep.parameters && ep.parameters.length > 0) {
        html += '<div class="doc-section">';
        html += '<h2 class="section-title">Parameters</h2>';
        html += '<table class="params-table">';
        html += '<thead><tr><th>Name</th><th>Type</th><th>Description</th></tr></thead>';
        html += '<tbody>';
        for (const param of ep.parameters) {
          html += '<tr>';
          html += '<td><span class="param-name">' + escapeHtml(param.name) + '</span>';
          if (param.required) html += '<span class="param-required">required</span>';
          html += '</td>';
          html += '<td><span class="param-type">' + escapeHtml(param.schema?.type || param.in) + '</span></td>';
          html += '<td>' + escapeHtml(param.description || '-') + '</td>';
          html += '</tr>';
        }
        html += '</tbody></table>';
        html += '</div>';
      }

      // Request Body
      if (ep.requestBody) {
        html += '<div class="doc-section">';
        html += '<h2 class="section-title">Request Body</h2>';
        if (ep.requestBody.description) {
          html += '<p style="color: var(--text-secondary); margin-bottom: 12px;">' + escapeHtml(ep.requestBody.description) + '</p>';
        }
        const content = ep.requestBody.content?.['application/json'];
        if (content?.schema) {
          // Get schema name from $ref or use 'Request Body'
          const schemaName = content.schema.$ref
            ? content.schema.$ref.replace('#/components/schemas/', '')
            : 'Request Body';
          html += renderSchema(content.schema, schemaName);
        }
        html += '</div>';
      }

      // Responses
      if (ep.responses) {
        html += '<div class="doc-section">';
        html += '<h2 class="section-title">Responses</h2>';
        for (const [code, response] of Object.entries(ep.responses)) {
          const codeClass = code.startsWith('2') ? '2xx' : code.startsWith('4') ? '4xx' : '5xx';
          html += '<div class="response-item" style="margin-bottom: 12px;">';
          html += '<div class="response-header" style="border-radius: 8px 8px 0 0;">';
          html += '<span class="response-code response-code-' + codeClass + '">' + code + '</span>';
          html += '<span style="flex: 1;">' + escapeHtml(response.description || '') + '</span>';
          html += '</div>';

          // Show response schema if available
          const responseContent = response.content?.['application/json'];
          if (responseContent?.schema) {
            html += '<div style="border: 1px solid var(--border-color); border-top: none; border-radius: 0 0 8px 8px; padding: 12px;">';
            const schemaName = responseContent.schema.$ref
              ? responseContent.schema.$ref.replace('#/components/schemas/', '')
              : 'Response ' + code;
            html += renderSchema(responseContent.schema, schemaName);
            html += '</div>';
          }
          html += '</div>';
        }
        html += '</div>';
      }

      document.getElementById('docPanel').innerHTML = html;
    }

    // Update code example
    function updateCodeExample(lang) {
      if (!currentEndpoint) return;
      const ep = currentEndpoint;
      const module = apiDocs.modules.find(m => m.id === ep.moduleId);
      const configuredBaseUrl = document.getElementById('baseUrlInput')?.value || config.baseUrl || window.location.origin;
      const baseUrl = configuredBaseUrl + module.basePath;

      let code = '';
      let bodyExample = '';

      // Generate body example if needed
      if (ep.requestBody) {
        const content = ep.requestBody.content?.['application/json'];
        if (content?.schema) {
          const example = generateExample(content.schema);
          bodyExample = JSON.stringify(example, null, 2);
        } else {
          bodyExample = '{}';
        }
      }

      var NL = String.fromCharCode(10);
      var BS = String.fromCharCode(92);

      if (lang === 'curl') {
        code = 'curl -X ' + ep.method + ' "' + baseUrl + ep.path + '"';
        code += ' ' + BS + NL + '  -H "Authorization: Bearer YOUR_TOKEN"';
        code += ' ' + BS + NL + '  -H "Content-Type: application/json"';
        if (ep.requestBody) {
          code += ' ' + BS + NL + "  -d '" + bodyExample + "'";
        }
      } else if (lang === 'javascript') {
        code = 'const response = await fetch("' + baseUrl + ep.path + '", {';
        code += NL + '  method: "' + ep.method + '",';
        code += NL + '  headers: {';
        code += NL + '    "Authorization": "Bearer YOUR_TOKEN",';
        code += NL + '    "Content-Type": "application/json"';
        code += NL + '  }';
        if (ep.requestBody) {
          code += ',' + NL + '  body: JSON.stringify(' + bodyExample + ')';
        }
        code += NL + '});' + NL + NL + 'const data = await response.json();';
      } else if (lang === 'python') {
        code = 'import requests';
        code += NL + NL + 'response = requests.' + ep.method.toLowerCase() + '(';
        code += NL + '    "' + baseUrl + ep.path + '",';
        code += NL + '    headers={';
        code += NL + '        "Authorization": "Bearer YOUR_TOKEN",';
        code += NL + '        "Content-Type": "application/json"';
        code += NL + '    }';
        if (ep.requestBody) {
          code += ',' + NL + '    json=' + bodyExample;
        }
        code += NL + ')' + NL + NL + 'data = response.json()';
      }

      document.getElementById('codeExamplesPanel').innerHTML =
        '<div class="code-block" style="margin: 8px;">' +
        '<div class="code-block-header">' +
        '<span class="code-block-title">' + lang.charAt(0).toUpperCase() + lang.slice(1) + ' Example</span>' +
        '<button class="copy-btn" id="copyBtn">Copy</button>' +
        '</div>' +
        '<pre>' + escapeHtml(code) + '</pre>' +
        '</div>';

      // Add copy button handler
      document.getElementById('copyBtn').onclick = function() {
        navigator.clipboard.writeText(code);
        this.textContent = 'Copied!';
        setTimeout(() => { this.textContent = 'Copy'; }, 2000);
      };
    }

    // Copy code
    function copyCode() {
      const pre = document.querySelector('#codeContent pre');
      if (pre) {
        navigator.clipboard.writeText(pre.textContent);
      }
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
    }

    // Escape HTML
    function escapeHtml(text) {
      if (!text) return '';
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    // Render description with safe HTML tags (b, br, code, pre, ul, li, ol, a)
    function renderDescription(text) {
      if (!text) return '';
      // First escape everything
      let html = escapeHtml(text);
      // Then restore safe HTML tags
      html = html.replace(/&lt;br&gt;/gi, '<br>');
      html = html.replace(/&lt;br\\/&gt;/gi, '<br>');
      html = html.replace(/&lt;br ?\\/&gt;/gi, '<br>');
      html = html.replace(/&lt;b&gt;/gi, '<b>');
      html = html.replace(/&lt;\\/b&gt;/gi, '</b>');
      html = html.replace(/&lt;code&gt;/gi, '<code>');
      html = html.replace(/&lt;\\/code&gt;/gi, '</code>');
      html = html.replace(/&lt;pre&gt;/gi, '<pre>');
      html = html.replace(/&lt;\\/pre&gt;/gi, '</pre>');
      html = html.replace(/&lt;ul&gt;/gi, '<ul>');
      html = html.replace(/&lt;\\/ul&gt;/gi, '</ul>');
      html = html.replace(/&lt;ol&gt;/gi, '<ol>');
      html = html.replace(/&lt;\\/ol&gt;/gi, '</ol>');
      html = html.replace(/&lt;li&gt;/gi, '<li>');
      html = html.replace(/&lt;\\/li&gt;/gi, '</li>');
      html = html.replace(/&lt;strong&gt;/gi, '<strong>');
      html = html.replace(/&lt;\\/strong&gt;/gi, '</strong>');
      html = html.replace(/&lt;em&gt;/gi, '<em>');
      html = html.replace(/&lt;\\/em&gt;/gi, '</em>');
      html = html.replace(/&lt;i&gt;/gi, '<i>');
      html = html.replace(/&lt;\\/i&gt;/gi, '</i>');
      return html;
    }
  </script>
</body>
</html>`;
}
