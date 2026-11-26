export const getIndexStatusIcon = (status: string): string => {
    const iconMap: Record<string, string> = {
      COMPLETED: 'check_circle',
      IN_PROGRESS: 'timelapse',
      FAILED: 'error_outline',
      NOT_STARTED: 'timer',
      QUEUED: 'hourglass_empty',
      PAUSED: 'pause_circle',
      FILE_TYPE_NOT_SUPPORTED: 'block',
      AUTO_INDEX_OFF: 'timer_off',
      EMPTY: 'draft',
      PASSWORD_PROTECTED: 'lock',
    };
    return iconMap[status] || 'help';
  };