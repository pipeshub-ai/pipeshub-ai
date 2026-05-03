'use client';

import React, { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { WorkspaceRightPanel } from '../../components';
import { useTeamsStore } from '../store';
import {
  CreateTeamForm,
  type CreateTeamFormHandle,
  type CreateTeamFormState,
} from '@/app/components/team';

/**
 * Workspace Teams page shell around the shared CreateTeamForm. The
 * WorkspaceRightPanel's built-in footer triggers the form via ref.
 */
export function CreateTeamSidebar({
  onCreateSuccess,
}: {
  onCreateSuccess?: () => void;
}) {
  const { t } = useTranslation();
  const isCreatePanelOpen = useTeamsStore((s) => s.isCreatePanelOpen);
  const closeCreatePanel = useTeamsStore((s) => s.closeCreatePanel);

  const formRef = useRef<CreateTeamFormHandle>(null);
  const [formState, setFormState] = useState<CreateTeamFormState>({
    isValid: false,
    isSubmitting: false,
  });

  const handleCreated = useCallback(() => {
    closeCreatePanel();
    onCreateSuccess?.();
  }, [closeCreatePanel, onCreateSuccess]);

  return (
    <WorkspaceRightPanel
      open={isCreatePanelOpen}
      onOpenChange={(open) => {
        if (!open) closeCreatePanel();
      }}
      title={t('workspace.teams.create.title', 'Create Team')}
      icon="groups"
      primaryLabel={t('workspace.teams.create.submit', 'Create Team')}
      secondaryLabel={t('workspace.teams.create.cancel', 'Cancel')}
      primaryDisabled={!formState.isValid}
      primaryLoading={formState.isSubmitting}
      onPrimaryClick={() => formRef.current?.submit()}
      onSecondaryClick={closeCreatePanel}
    >
      {isCreatePanelOpen && (
        <CreateTeamForm
          ref={formRef}
          enabled={isCreatePanelOpen}
          onCreated={handleCreated}
          onStateChange={setFormState}
        />
      )}
    </WorkspaceRightPanel>
  );
}
