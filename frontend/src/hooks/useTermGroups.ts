import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Course } from '../types';
import { groupCoursesByTerm, pickCurrentTermKey, type TermGroup } from '../utils/course';

export type UseTermGroups = {
  groups: TermGroup[];
  currentKey: string | null;
  isExpanded: (key: string) => boolean;
  toggle: (key: string) => void;
};

/**
 * Groups courses by term and tracks which term sections are expanded. On first
 * load the current term is expanded and all others are collapsed; later course
 * refreshes (e.g. after a sync) preserve whatever the user has toggled.
 */
export function useTermGroups(courses: Course[]): UseTermGroups {
  const groups = useMemo(() => groupCoursesByTerm(courses), [courses]);
  const currentKey = useMemo(() => pickCurrentTermKey(groups), [groups]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const initialized = useRef(false);

  useEffect(() => {
    if (initialized.current || currentKey == null) return;
    setExpanded(new Set([currentKey]));
    initialized.current = true;
  }, [currentKey]);

  const toggle = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const isExpanded = useCallback((key: string) => expanded.has(key), [expanded]);

  return { groups, currentKey, isExpanded, toggle };
}
