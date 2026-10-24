'use client'

import { Box, Flex } from '@radix-ui/themes'
import { ReactNode } from 'react'
import WorkspaceSidebar from './sidebar'
import { SidebarExpandButton } from '@/app/components/sidebar/sidebar-expand-button'
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store'

export default function WorkspaceLayout({
  children,
}: {
  children: ReactNode
}) {
  const isNavCollapsed = useSidebarWidthStore((s) => s.isNavCollapsed)
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
        {/* Its own control keeps the shell's fixed fallback from overlapping the sidebar header. */}
        <Flex px="3" pt="2" style={{ display: isNavCollapsed ? 'flex' : 'contents' }}>
          <SidebarExpandButton placement="inline" />
        </Flex>
        {children}
      </Box>
    </Flex>
  )
}
