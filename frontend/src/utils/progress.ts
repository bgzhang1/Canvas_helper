import type { AnalysisStatus, SyncProgress, SyncStatus } from '../types';

export function parseSyncProgress(syncStatus: SyncStatus): SyncProgress {
  if (syncStatus.run?.counts_json) {
    try {
      const counts = JSON.parse(syncStatus.run.counts_json) as { progress?: Partial<SyncProgress> };
      const progress = counts.progress ?? {};
      return {
        percent: typeof progress.percent === 'number' && Number.isFinite(progress.percent) ? progress.percent : syncStatus.run.status === 'succeeded' ? 100 : 0,
        stage: progress.stage || syncStatus.run.message || syncStatus.run.status,
        current: typeof progress.current === 'number' ? progress.current : undefined,
        total: typeof progress.total === 'number' ? progress.total : undefined,
        course: progress.course ?? null,
        file: progress.file ?? null,
        phase: progress.phase,
        status: progress.status || syncStatus.run.status
      };
    } catch {
      return {
        percent: syncStatus.run.status === 'succeeded' ? 100 : 0,
        stage: syncStatus.run.message || syncStatus.run.status,
        status: syncStatus.run.status
      };
    }
  }
  return {
    percent: syncStatus.running ? 4 : 0,
    stage: syncStatus.running ? 'Starting sync' : 'idle',
    status: syncStatus.running ? 'running' : 'idle'
  };
}

export function idleAnalysisStatus(): AnalysisStatus {
  return {
    running: false,
    status: 'idle',
    percent: 0,
    stage: 'Idle',
    course_id: null,
    course: null,
    file: null,
    current: null,
    total: null,
    message: null
  };
}

export function analysisStatusToProgress(status: AnalysisStatus): SyncProgress {
  return {
    percent: status.percent,
    stage: status.stage,
    current: typeof status.current === 'number' ? status.current : undefined,
    total: typeof status.total === 'number' ? status.total : undefined,
    course: status.course ?? null,
    file: status.message || status.file || null,
    status: status.status
  };
}
