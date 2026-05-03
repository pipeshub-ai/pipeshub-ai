/** Patterns chokidar should not emit events for. */
export const IGNORED_PATTERNS: RegExp[] = [
  /(?:^|[/\\])\.DS_Store$/,
  /(?:^|[/\\])Thumbs\.db$/,
  /(?:^|[/\\])desktop\.ini$/,
  /\.swp$/, /\.swo$/, /~$/,
  /(?:^|[/\\])\.#/, /#$/,
  /(?:^|[/\\])___jb_\w+___$/,
  /\.crswap$/,
  /\.tmp$/,
  /(?:^|[/\\])\.git(?:[/\\]|$)/,
  /(?:^|[/\\])node_modules(?:[/\\]|$)/,
  /(?:^|[/\\])__pycache__(?:[/\\]|$)/,
  /(?:^|[/\\])\.venv(?:[/\\]|$)/,
];
