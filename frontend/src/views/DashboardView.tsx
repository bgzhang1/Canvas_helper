import { BookOpen, Database } from 'lucide-react';
import { useMemo } from 'react';
import type { Course, SyncStatus } from '../types';
import { useAppContext } from '../context/AppContext';
import { Badge, EmptyState } from '../components/ui';
import { courseNumber, statusForCourse } from '../utils/course';
import { courseStatusLabel, syncStatusLabel } from '../utils/labels';

export function DashboardView({ courses, syncStatus, onSelectCourse }: { courses: Course[]; syncStatus: SyncStatus; onSelectCourse: (course: Course) => void | Promise<void> }) {
  const { t } = useAppContext();
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
        <EmptyState>{t('noCourses')}</EmptyState>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {courses.map((course) => {
            const courseStatus = statusForCourse(course);
            return (
              <div
                key={course.id}
                onClick={() => onSelectCourse(course)}
                className="group bg-[#F4F4F0] border border-black p-8 cursor-pointer hover:bg-black hover:text-[#F4F4F0] transition-colors duration-150 relative"
              >
                <div className="flex justify-between items-start mb-8">
                  <BookOpen size={32} strokeWidth={1} className="group-hover:text-[#F4F4F0] text-black" />
                  <div className="flex gap-2">
                    {course.announcement_count > 0 && <Badge variant="default">{course.announcement_count} {t('news')}</Badge>}
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
          })}
        </div>
      )}
    </div>
  );
}
