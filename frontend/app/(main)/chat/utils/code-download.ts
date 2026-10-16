import { saveTextFile } from './save-text-file';

// Only languages whose file name is well known. Anything else saves as `.txt`, so a block the model
// labelled with a made-up or executable-looking language never gets an extension the OS would run.
const EXTENSION_BY_LANGUAGE: Record<string, string> = {
  javascript: 'js',
  js: 'js',
  jsx: 'jsx',
  typescript: 'ts',
  ts: 'ts',
  tsx: 'tsx',
  python: 'py',
  py: 'py',
  java: 'java',
  c: 'c',
  cpp: 'cpp',
  'c++': 'cpp',
  csharp: 'cs',
  cs: 'cs',
  go: 'go',
  rust: 'rs',
  rs: 'rs',
  ruby: 'rb',
  rb: 'rb',
  php: 'php',
  swift: 'swift',
  kotlin: 'kt',
  scala: 'scala',
  dart: 'dart',
  lua: 'lua',
  r: 'r',
  sql: 'sql',
  html: 'html',
  css: 'css',
  scss: 'scss',
  json: 'json',
  yaml: 'yaml',
  yml: 'yaml',
  xml: 'xml',
  toml: 'toml',
  markdown: 'md',
  md: 'md',
  bash: 'sh',
  sh: 'sh',
  shell: 'sh',
  zsh: 'sh',
  powershell: 'ps1',
  diff: 'diff',
  graphql: 'graphql',
};

export function codeFileName(language: string): string {
  const key = language.trim().toLowerCase().replace(/^language-/, '');
  // A plain lookup would find `__proto__` or `constructor` on Object.prototype.
  const known = Object.prototype.hasOwnProperty.call(EXTENSION_BY_LANGUAGE, key);
  return `code.${known ? EXTENSION_BY_LANGUAGE[key] : 'txt'}`;
}

export function downloadCode(codeText: string, language: string): void {
  const text = codeText.endsWith('\n') ? codeText : `${codeText}\n`;
  saveTextFile(codeFileName(language), text, 'text/plain');
}
