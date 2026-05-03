'use client'

import { Box, Flex } from '@radix-ui/themes'
import { ReactNode } from 'react'
import WorkspaceSidebar from './sidebar'

/**
 * Workspace settings shell: left drawer + scrollable page area.
 *
 * The drawer lives here (not only in the @sidebar parallel slot) so deep links
 * like /workspace/developer-settings/oauth2/ always show navigation ΓÇö parallel
 * @sidebar segments do not reliably match on hard navigation for nested paths.
 */
export default function WorkspaceLayout({
  children,
}: {
  children: ReactNode
}) {
  return (
    <Flex style={{ height: '100%', minHeight: 0, overflow: 'hidden' }}>
      <WorkspaceSidebar />
      <Box
        className="no-scrollbar"
        style={{
          flex: 1,
          minWidth: 0,
          height: '100%',
          overflowY: 'auto',
          overflowX: 'hidden',
          background:
            'linear-gradient(180deg, var(--olive-2, #181917) 0%, var(--olive-1, #111210) 100%)',
        }}
      >
        {children}
      </Box>
    </Flex>
  )
}
