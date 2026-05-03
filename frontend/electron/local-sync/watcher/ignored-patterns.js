const IGNORED_PATTERNS = [
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

module.exports = { IGNORED_PATTERNS };
