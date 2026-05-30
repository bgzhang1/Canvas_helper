import type { MaterialFile } from '../types';
import { api, downloadSelection } from './client';

export type FileOperationResult = {
  status: string;
  backup?: Record<string, number>;
  extraction?: Record<string, number>;
  counts?: Record<string, number>;
};

export function backupSelectedFiles(courseId: number, fileIds: number[]): Promise<FileOperationResult> {
  return api<FileOperationResult>(`/api/courses/${courseId}/files/backup`, {
    method: 'POST',
    body: JSON.stringify({ file_ids: fileIds })
  });
}

export function syncCourseFiles(courseId: number): Promise<FileOperationResult> {
  return api<FileOperationResult>(`/api/courses/${courseId}/files/sync`, { method: 'POST' });
}

export function backupFile(courseId: number, file: MaterialFile): Promise<FileOperationResult> {
  return api<FileOperationResult>(`/api/courses/${courseId}/files/${file.id}/backup`, { method: 'POST' });
}

export function extractFile(courseId: number, file: MaterialFile): Promise<FileOperationResult> {
  return api<FileOperationResult>(`/api/courses/${courseId}/files/${file.id}/extract`, { method: 'POST' });
}

export function downloadSelectedFiles(courseId: number, fileIds: number[]): Promise<void> {
  return downloadSelection(courseId, fileIds);
}
