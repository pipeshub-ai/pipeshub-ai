'use client';

import { Suspense, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Flex } from '@radix-ui/themes';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import {
  useFeatureFlagsStore,
  selectProjectsEnabled,
  selectFeatureFlagsLoaded,
} from '@/lib/store/feature-flags-store';
import { ProjectList } from './components/project-list';
import { ProjectWorkspaceRedesigned } from './components/project-workspace-redesigned';

function ProjectsPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const rawProjectId = searchParams.get('projectId');
  const projectId = rawProjectId?.trim() ? rawProjectId : null;

  const flagsLoaded = useFeatureFlagsStore(selectFeatureFlagsLoaded);
  const projectsEnabled = useFeatureFlagsStore(selectProjectsEnabled);

  useEffect(() => {
    if (flagsLoaded && !projectsEnabled) {
      router.replace('/chat/');
    }
  }, [flagsLoaded, projectsEnabled, router]);

  if (!flagsLoaded || !projectsEnabled) {
    return (
      <Flex align="center" justify="center" style={{ width: '100%', height: '100%' }}>
        <LottieLoader autoplay loop style={{ width: 48, height: 48 }} />
      </Flex>
    );
  }

  return projectId ? <ProjectWorkspaceRedesigned projectId={projectId} /> : <ProjectList />;
}

export default function ProjectsPage() {
  return (
    <ServiceGate services={['query']}>
      <Suspense>
        <ProjectsPageContent />
      </Suspense>
    </ServiceGate>
  );
}
