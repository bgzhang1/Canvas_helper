import type { Course, CourseDetail } from '../types';
import type { AnalysisStatus } from '../types';
import { api } from './client';

export function fetchCourses(): Promise<Course[]> {
  return api<Course[]>('/api/courses');
}

export function fetchCourseDetail(courseId: number): Promise<CourseDetail> {
  return api<CourseDetail>(`/api/courses/${courseId}/detail`);
}

export function startCourseSync(courseId: number): Promise<{ status: string; run_id?: number; run?: unknown }> {
  return api(`/api/courses/${courseId}/sync`, { method: 'POST' });
}

export function startCourseAnalysis(courseId: number): Promise<{ status: string; progress?: AnalysisStatus }> {
  return api(`/api/courses/${courseId}/analyze`, { method: 'POST' });
}
