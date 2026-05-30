import { useCallback, useEffect, useState } from 'react';
import type { Course } from '../types';

const STORAGE_KEY = 'canvasSeenAnnouncements';

function read(): Record<number, number> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') as Record<number, number>;
  } catch {
    return {};
  }
}

function persist(value: Record<number, number>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    /* ignore storage errors */
  }
}

/**
 * Tracks how many announcements per course the user has already seen (persisted
 * in localStorage). Newly discovered courses are baselined to their current
 * count so only later increases light up the "new" dot.
 */
export function useAnnouncementsSeen(courses: Course[]) {
  const [seenAnnouncements, setSeenAnnouncements] = useState<Record<number, number>>(read);

  const markCourseAnnouncementsSeen = useCallback((courseId: number, count: number) => {
    setSeenAnnouncements((prev) => {
      if (prev[courseId] === count) return prev;
      const next = { ...prev, [courseId]: count };
      persist(next);
      return next;
    });
  }, []);

  useEffect(() => {
    if (courses.length === 0) return;
    setSeenAnnouncements((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const course of courses) {
        if (!(course.id in next)) {
          next[course.id] = course.announcement_count;
          changed = true;
        }
      }
      if (changed) persist(next);
      return changed ? next : prev;
    });
  }, [courses]);

  return { seenAnnouncements, markCourseAnnouncementsSeen };
}
