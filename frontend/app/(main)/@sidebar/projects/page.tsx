'use client';

import { Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { useFeatureFlagsStore, selectProjectsEnabled } from '@/lib/store/feature-flags-store';
import { ProjectsSidebar } from '../../projects/sidebar';
import { ProjectConversationsSidebar } from '../../chat/sidebar/project-conversations-sidebar';

/**
 * `/projects` sidebar slot — shows this project's recent conversations once
 * a project is selected (`?projectId=`, the workspace itself has no
 * `conversationId` concept), otherwise the full all-projects list.
 */
function ProjectsSidebarSlotInner() {
  const searchParams = useSearchParams();
  const projectId = searchParams.get('projectId');
  const projectsEnabled = useFeatureFlagsStore(selectProjectsEnabled);

  // The page itself redirects to /chat when the flag is off; avoid a flash
  // of either sidebar (and their fetches) during that transition.
  if (!projectsEnabled) return null;
  if (projectId?.trim()) {
    return <ProjectConversationsSidebar projectId={projectId} />;
  }
  return <ProjectsSidebar />;
}

export default function ProjectsSidebarSlot() {
  return (
    <Suspense>
      <ProjectsSidebarSlotInner />
    </Suspense>
  );
}
