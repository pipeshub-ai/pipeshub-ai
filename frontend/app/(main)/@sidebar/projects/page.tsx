'use client';

import { Suspense } from 'react';
import { ProjectsSidebar } from '../../projects/sidebar';

export default function ProjectsSidebarSlot() {
  return (
    <Suspense>
      <ProjectsSidebar />
    </Suspense>
  );
}
