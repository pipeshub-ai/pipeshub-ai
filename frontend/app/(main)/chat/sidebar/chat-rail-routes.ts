/**
 * The pages that show the chat rail: Chat (with its agent and project views), the Agents page and All artifacts. The
 * app's layout keeps one rail across them, so moving between them rebuilds and reloads nothing. The agent builder
 * (`/agents/edit`, `/agents/new`) shows no rail.
 */
export function isChatRailPath(pathname: string | null): boolean {
  const path = (pathname ?? '').replace(/\/+$/, '');
  return path === '/chat' || path.startsWith('/chat/') || path === '/agents' || path === '/artifacts' || path.startsWith('/artifacts/');
}
