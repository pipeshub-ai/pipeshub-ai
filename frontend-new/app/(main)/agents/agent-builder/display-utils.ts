export function normalizeDisplayName(name: string): string {
  return name
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');
}

export function formattedProvider(provider: string): string {
  switch (provider) {
    case 'azureOpenAI':
      return 'Azure OpenAI';
    case 'openAI':
      return 'OpenAI';
    case 'anthropic':
      return 'Anthropic';
    case 'gemini':
      return 'Gemini';
    case 'ollama':
      return 'Ollama';
    case 'bedrock':
      return 'AWS Bedrock';
    case 'xai':
      return 'xAI';
    case 'groq':
      return 'Groq';
    case 'mistral':
      return 'Mistral';
    case 'openAICompatible':
      return 'OpenAI API Compatible';
    default:
      return provider || 'AI';
  }
}

export function truncateText(text: string, maxLength = 50): string {
  if (text.length <= maxLength) return text;
  return `${text.substring(0, maxLength)}…`;
}

export function getAppDisplayName(appName: string): string {
  return normalizeDisplayName(appName.replace(/\./g, '_'));
}

/** Material icon name for tool rows in sidebar */
export function getAppIconName(appName: string): string {
  const key = appName.toLowerCase();
  if (key.includes('slack')) return 'chat';
  if (key.includes('gmail') || key.includes('mail')) return 'mail';
  if (key.includes('drive')) return 'cloud';
  if (key.includes('jira')) return 'assignment';
  if (key.includes('github')) return 'code';
  if (key.includes('confluence')) return 'description';
  return 'extension';
}
