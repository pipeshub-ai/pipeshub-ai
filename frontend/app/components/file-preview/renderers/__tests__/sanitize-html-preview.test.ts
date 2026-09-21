import { describe, it, expect } from 'vitest';
import { sanitizeHtmlPreview } from '../sanitize-html-preview';

const PIZZA_PAGE = `<!DOCTYPE html>
<html>
<head>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Space+Grotesk&display=swap" rel="stylesheet">
  <style>
    body { background: #0a0a0a; color: #f5f0e8; }
    .hero { display: grid; }
  </style>
</head>
<body>
  <h1>Good pizza. Bad hours.</h1>
  <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E" alt="pizza">
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="120" height="120">
    <circle cx="50" cy="50" r="40" fill="#f5c518"></circle>
    <path d="M20 50 L80 50" stroke="#c23" stroke-width="4"></path>
  </svg>
  <script>alert('xss')</script>
  <a href="javascript:alert(1)">nope</a>
</body>
</html>`;

describe('sanitizeHtmlPreview', () => {
  it('keeps document CSS, webfonts, images, and inline SVG', () => {
    const clean = sanitizeHtmlPreview(PIZZA_PAGE);

    expect(clean).toContain('<style>');
    expect(clean).toContain('background: #0a0a0a');
    expect(clean).toContain('fonts.googleapis.com');
    expect(clean).toContain('rel="stylesheet"');
    expect(clean).toContain('<img');
    expect(clean).toMatch(/<svg/i);
    expect(clean).toMatch(/<circle/i);
    expect(clean).toMatch(/<path/i);
    expect(clean).toContain('Good pizza. Bad hours.');
  });

  it('strips script tags and javascript URLs', () => {
    const clean = sanitizeHtmlPreview(PIZZA_PAGE);

    expect(clean.toLowerCase()).not.toContain('<script');
    expect(clean).not.toContain('alert(');
    expect(clean).not.toMatch(/javascript:/i);
  });
});
