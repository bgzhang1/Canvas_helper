import { BookOpen, Database, RefreshCcw } from 'lucide-react';
import { useMemo } from 'react';
import type { Course, SyncStatus } from '../types';
import { useAppContext } from '../context/AppContext';
import { Badge, EmptyState } from '../components/ui';
import { courseNumber, groupCoursesByTerm, pickCurrentTermKey, statusForCourse } from '../utils/course';
import { courseStatusLabel, syncStatusLabel } from '../utils/labels';

export function DashboardView({ courses, syncStatus, seenAnnouncements, onSelectCourse, onSync, syncActive }: { courses: Course[]; syncStatus: SyncStatus; seenAnnouncements: Record<number, number>; onSelectCourse: (course: Course) => void | Promise<void>; onSync?: () => void; syncActive?: boolean }) {
  const { t, query } = useAppContext();
  const searching = query.trim().length > 0;
  const groups = useMemo(() => groupCoursesByTerm(courses), [courses]);
  const currentKey = useMemo(() => pickCurrentTermKey(groups), [groups]);
  const currentGroup = useMemo(() => groups.find((group) => group.key === currentKey) ?? null, [groups, currentKey]);
  // Dashboard shows only the current term. A search is an explicit action, so it
  // falls back to every matching course across all terms.
  const visible = searching ? courses : currentGroup?.courses ?? [];
  const totals = useMemo(
    () =>
      courses.reduce(
        (acc, course) => ({
          announcements: acc.announcements + course.announcement_count,
          assignments: acc.assignments + course.assignment_count,
          files: acc.files + course.file_count
        }),
        { announcements: 0, assignments: 0, files: 0 }
      ),
    [courses]
  );

  function renderCourseCard(course: Course) {
    const courseStatus = statusForCourse(course);
    const hasNewAnnouncements = course.announcement_count > (seenAnnouncements[course.id] ?? course.announcement_count);
    return (
      <div
        key={course.id}
        onClick={() => onSelectCourse(course)}
        className="group bg-[#F4F4F0] border border-black p-8 cursor-pointer hover:bg-black hover:text-[#F4F4F0] transition-colors duration-150 relative"
      >
        <div className="flex justify-between items-start mb-8">
          <BookOpen size={32} strokeWidth={1} className="group-hover:text-[#F4F4F0] text-black" />
          <div className="flex gap-2">
            {(course.upcoming_count > 0 || hasNewAnnouncements) && (
              <span className="relative inline-flex">
                <Badge variant="default">{course.upcoming_count} {t('news')}</Badge>
                {hasNewAnnouncements && (
                  <span className="absolute -top-1.5 -right-1.5 h-2.5 w-2.5 rounded-full bg-red-600 ring-2 ring-[#F4F4F0]" aria-label="new announcements" />
                )}
              </span>
            )}
            <Badge variant={courseStatus === 'SYNCED' ? 'inverted' : 'warning'}>{courseStatusLabel(courseStatus, t)}</Badge>
          </div>
        </div>

        <h2 className="text-3xl font-bold tracking-tight mb-2">{courseNumber(course)}</h2>
        <p className="text-sm text-gray-600 group-hover:text-gray-300 font-medium mb-12 line-clamp-2">{course.name}</p>

        <div className="flex items-center justify-between gap-4 text-[11px] font-mono border-t border-black group-hover:border-[#F4F4F0] pt-4">
          <span className="flex items-center gap-2">
            <Database size={12} />
            {course.file_count} {t('filesUnit')}
          </span>
          <span>{course.term_name || t('noTerm')}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex items-end justify-between mb-12 border-b border-black pb-6">
        <div>
          <h1 className="dashboard-title text-5xl font-bold tracking-tighter mb-4">{t('workspaceTitle')}</h1>
          <p className="text-sm font-mono text-gray-600">
            {t('cacheMounted')} {courses.length} {t('courses')}, {totals.assignments} {t('assignmentsUnit')}, {totals.files} {t('filesUnit')}.
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <Badge variant="inverted">{syncStatus.running ? t('syncRunning') : t('dataReady')}</Badge>
          {syncStatus.run && <span className="text-[10px] font-mono text-gray-500">{t('last')}: {syncStatusLabel(syncStatus.run.status, t)}</span>}
        </div>
      </div>

      {courses.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <div className="mb-6 animate-pulse">
            <RefreshCcw size={48} strokeWidth={1} className="text-gray-400" />
          </div>
          <h2 className="text-2xl font-bold tracking-tight mb-3">{t('noCourses')}</h2>
          {onSync && (
            <button
              type="button"
              onClick={onSync}
              disabled={syncActive}
              className="mt-6 px-8 py-3 border-2 border-black bg-black text-[#F4F4F0] font-mono text-sm tracking-widest uppercase hover:bg-[#F4F4F0] hover:text-black transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {syncActive ? t('syncing') : t('forceSync')}
            </button>
          )}
        </div>
      ) : (
        <>
          {!searching && currentGroup && (
            <div className="flex items-center justify-between gap-4 mb-6 border border-black bg-black text-[#F4F4F0] px-6 py-3">
              <span className="font-mono font-bold tracking-widest text-sm truncate">{currentGroup.termName ?? t('noTerm')}</span>
              <span className="flex items-center gap-3 shrink-0">
                <Badge variant="default">{t('currentTermBadge')}</Badge>
                <span className="text-[11px] font-mono tracking-widest text-gray-300">{currentGroup.courses.length} {t('courses')}</span>
              </span>
            </div>
          )}
          {visible.length === 0 ? (
            <EmptyState>{t('noCourses')}</EmptyState>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">{visible.map(renderCourseCard)}</div>
          )}
        </>
      )}
    </div>
  );
}
