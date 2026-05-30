import type { Course } from '../types';

export function courseCode(course: Course) {
  return course.course_code || `COURSE_${course.id}`;
}

export function courseNumber(course: Course) {
  const candidates = [course.name, course.course_code].filter(Boolean);
  for (const candidate of candidates) {
    const match = candidate!.match(/\b[A-Z]{2,}[A-Z0-9]*\d{3,}[A-Z0-9]*\b/);
    if (match) return match[0];
  }
  return courseCode(course);
}

export function statusForCourse(course: Course) {
  if (course.file_count === 0) return 'SYNCED';
  if (course.downloaded_count >= course.file_count) return 'SYNCED';
  if (course.downloaded_count > 0) return 'PARTIAL';
  return 'INDEXED';
}
