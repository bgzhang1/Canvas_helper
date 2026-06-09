import type { EventLogFilter, EventLogLevel } from '../types';
import type { TFunction, TranslationKey } from '../i18n';

export function courseStatusLabel(status: string, t: TFunction) {
  const normalized = status.toLowerCase();
  if (normalized === 'synced') return t('statusSynced');
  if (normalized === 'partial') return t('statusPartial');
  if (normalized === 'indexed') return t('statusIndexed');
  return status;
}

export function syncStatusLabel(status: string | null | undefined, t: TFunction) {
  const normalized = (status || 'idle').toLowerCase();
  if (normalized === 'idle') return t('statusIdle');
  if (normalized === 'running') return t('statusRunning');
  if (normalized === 'succeeded') return t('statusSucceeded');
  if (normalized === 'failed') return t('statusFailedPlain');
  if (normalized === 'cancelled') return t('statusCancelledPlain');
  if (normalized === 'interrupted') return t('statusInterrupted');
  return status || t('statusIdle');
}

export function syncStageLabel(stage: string | null | undefined, t: TFunction) {
  if (!stage) return t('stageIdle');
  if (stage === 'Starting sync') return t('stageStartingSync');
  if (stage === 'idle') return t('stageIdle');
  if (stage === 'Connecting to Canvas') return t('stageConnectingCanvas');
  if (stage === 'Metadata sync completed; downloading course files in background') return t('stageMetadataFiles');
  if (stage === 'Background courseware download') return t('stageBackgroundDownload');
  if (stage === 'Indexed downloaded course files') return t('stageIndexedFiles');
  if (stage === 'Sync completed') return t('stageSyncCompleted');
  if (stage === 'Sync interrupted') return t('stageSyncInterrupted');
  if (stage === 'Interrupt requested') return t('stageInterruptRequested');
  if (/^Fetched \d+ courses$/.test(stage)) return `${t('stageFetchedCourses')} ${stage.match(/\d+/)?.[0] ?? ''}`.trim();
  return stage;
}

export function eventStatusLabel(status: string, t: TFunction) {
  const normalized = status.toLowerCase();
  if (normalized === 'success' || normalized === 'succeeded') return t('logStatusSuccess');
  if (normalized === 'failed' || normalized === 'error') return t('logStatusFailed');
  if (['warning', 'warn', 'partial', 'cancelled', 'interrupted', 'skipped'].includes(normalized)) return t('logStatusWarning');
  if (normalized === 'running') return t('logStatusRunning');
  return status;
}

export function eventLogLevel(status: string): EventLogLevel {
  const normalized = status.toLowerCase();
  if (normalized === 'success' || normalized === 'succeeded') return 'success';
  if (normalized === 'failed' || normalized === 'error') return 'failed';
  if (['warning', 'warn', 'partial', 'cancelled', 'interrupted', 'skipped'].includes(normalized)) return 'warning';
  if (normalized === 'running') return 'running';
  return 'other';
}

export function eventLogFilterLabel(filter: EventLogFilter, t: TFunction) {
  if (filter === 'all') return t('logFilterAll');
  if (filter === 'success') return t('logFilterSuccess');
  if (filter === 'failed') return t('logFilterFailed');
  return t('logFilterWarning');
}

export function eventLogBadgeVariant(status: string): 'default' | 'inverted' | 'warning' | 'danger' {
  const level = eventLogLevel(status);
  if (level === 'success') return 'inverted';
  if (level === 'failed') return 'danger';
  if (level === 'warning') return 'warning';
  return 'default';
}

export function eventCategoryLabel(category: string, t: TFunction) {
  const normalized = category.toLowerCase();
  if (normalized === 'sync') return t('logCategorySync');
  if (normalized === 'file') return t('logCategoryFile');
  if (normalized === 'agent') return t('logCategoryAgent');
  if (normalized === 'announcement') return t('logCategoryAnnouncement');
  if (normalized === 'assignment') return t('logCategoryAssignment');
  return category;
}

export function eventActionLabel(action: string, t: TFunction) {
  const labels: Record<string, TranslationKey> = {
    sync_started: 'actionSyncStarted',
    sync_completed: 'actionSyncCompleted',
    sync_failed: 'actionSyncFailed',
    sync_cancelled: 'actionSyncCancelled',
    course_synced: 'actionCourseSynced',
    announcement_synced: 'actionAnnouncementSynced',
    assignment_synced: 'actionAssignmentSynced',
    file_indexed: 'actionFileIndexed',
    file_downloaded: 'actionFileDownloaded',
    file_extracted: 'actionFileExtracted',
    file_sync_started: 'actionFileSyncStarted',
    file_sync_completed: 'actionFileSyncCompleted',
    file_sync_failed: 'actionFileSyncFailed',
    file_backup_started: 'actionFileBackupStarted',
    file_backup_completed: 'actionFileBackupCompleted',
    file_backup_failed: 'actionFileBackupFailed',
    agent_chat_started: 'actionAgentChatStarted',
    agent_chat_completed: 'actionAgentChatCompleted',
    agent_chat_failed: 'actionAgentChatFailed',
    agent_chat_not_configured: 'actionAgentChatNotConfigured',
    agent_tool_call: 'actionAgentToolCall'
  };
  return labels[action] ? t(labels[action]) : action;
}
