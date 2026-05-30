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

export type TermGroup = {
  key: string;
  termName: string | null;
  startAt: string | null;
  endAt: string | null;
  courses: Course[];
};

const NO_TERM_KEY = '__no_term__';

/** Group courses by their Canvas term, newest term first; courses without a term last. */
export function groupCoursesByTerm(courses: Course[]): TermGroup[] {
  const groups = new Map<string, TermGroup>();
  for (const course of courses) {
    const key = course.term_name ?? NO_TERM_KEY;
    let group = groups.get(key);
    if (!group) {
      group = { key, termName: course.term_name, startAt: course.term_start_at ?? null, endAt: course.term_end_at ?? null, courses: [] };
      groups.set(key, group);
    }
    if (!group.startAt && course.term_start_at) group.startAt = course.term_start_at;
    if (!group.endAt && course.term_end_at) group.endAt = course.term_end_at;
    group.courses.push(course);
  }
  return Array.from(groups.values()).sort((a, b) => {
    if (a.startAt && b.startAt) return a.startAt < b.startAt ? 1 : a.startAt > b.startAt ? -1 : 0;
    if (a.startAt) return -1;
    if (b.startAt) return 1;
    return 0;
  });
}

/**
 * The term to expand by default: the one whose date range contains today; else
 * the most recently started term (groups are sorted newest-first); else the first.
 */
export function pickCurrentTermKey(groups: TermGroup[]): string | null {
  if (groups.length === 0) return null;
  const now = new Date().toISOString();
  const containing = groups.find((group) => group.startAt != null && group.startAt <= now && (group.endAt == null || group.endAt >= now));
  if (containing) return containing.key;
  const started = groups.find((group) => group.startAt != null && group.startAt <= now);
  if (started) return started.key;
  return groups[0].key;
}
