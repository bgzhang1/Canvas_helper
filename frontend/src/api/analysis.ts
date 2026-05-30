import type { AnalysisStatus } from '../types';
import { api } from './client';

export function fetchAnalysisStatus(): Promise<AnalysisStatus> {
  return api<AnalysisStatus>('/api/analysis/status');
}
