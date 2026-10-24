'use client'

import { Box, Flex } from '@radix-ui/themes'
import { ReactNode } from 'react'
import WorkspaceSidebar from './sidebar'

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
