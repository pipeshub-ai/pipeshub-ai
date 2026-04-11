'use client'

import { Box } from '@radix-ui/themes'
import { ReactNode } from 'react'

export default function WorkspaceLayout({
  children,
}: {
  children: ReactNode
}) {
  return (
    <Box
      style={{
        height: '100%',
        overflowY: 'auto',
        background:
          'linear-gradient(180deg, var(--olive-2, #181917) 0%, var(--olive-1, #111210) 100%)',
      }}
    >
      {children}
    </Box>
  )
}
