import type { SyncStatus } from '../types';
import { api } from './client';

export function fetchSyncStatus(): Promise<SyncStatus> {
  return api<SyncStatus>('/api/sync/status');
}

export function startGlobalSync(): Promise<{ status: string; run_id?: number; run?: unknown }> {
  return api('/api/sync/run', { method: 'POST' });
}

export function cancelSync(): Promise<{ status: string; run?: unknown }> {
  return api('/api/sync/cancel', { method: 'POST' });
}
