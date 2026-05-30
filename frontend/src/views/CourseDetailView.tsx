import { useState } from 'react';
import { Bell, CalendarDays, ChevronDown, ChevronUp, ClipboardList, FileText, RefreshCcw, Sparkles, Users } from 'lucide-react';
import type { ActiveTab, Course, CourseDetail } from '../types';
import { useAppContext } from '../context/AppContext';
import { Badge, EmptyState } from '../components/ui';
import { CanvasHomePanel } from '../components/CanvasHomePanel';
import { courseNumber } from '../utils/course';
import { TimelineTab } from './course/TimelineTab';
import { FilesTab } from './course/FilesTab';
import { AnnouncementsTab } from './course/AnnouncementsTab';
import { AssignmentsTab } from './course/AssignmentsTab';
import { PeopleTab } from './course/PeopleTab';

export function CourseDetailView({
  course,
  detail,
  activeTab,
  setActiveTab,
  loading,
  refreshCourse,
  onSyncCourse,
  syncActive,
  onAnalyzeCourse,
  analysisActive,
  analysisCourseId
}: {
  course: Course;
  detail: CourseDetail | null;
  activeTab: ActiveTab;
  setActiveTab: (tab: ActiveTab) => void;
  loading: boolean;
  refreshCourse: () => Promise<void>;
  onSyncCourse: (course: Course) => Promise<void>;
  syncActive: boolean;
  onAnalyzeCourse: (course: Course) => Promise<void>;
  analysisActive: boolean;
  analysisCourseId: number | null;
}) {
  const { busy, t } = useAppContext();
  const [isHomeExpanded, setIsHomeExpanded] = useState(false);
  const analyzingThisCourse = analysisActive && analysisCourseId === course.id;

  return (
    <div className="course-detail-root max-w-5xl mx-auto w-full relative">
      <div className="course-scroll-panel pb-12">
        <div className="mb-12 border-b border-black pb-8">
          <div className="flex items-start justify-between mb-4 gap-6">
            <div className="flex items-center gap-4 min-w-0">
              <h1 className="course-title text-6xl font-bold tracking-tighter truncate">{courseNumber(course)}</h1>
              <div className="flex flex-col gap-1 mt-2">
                <Badge variant="inverted">{course.term_name || t('noTermCompact')}</Badge>
                <button
                  onClick={() => onSyncCourse(course)}
                  disabled={syncActive || loading}
                  className="inline-flex items-center justify-center gap-1 border border-black px-2 py-0.5 text-[10px] font-mono tracking-widest uppercase bg-[#F4F4F0] text-black hover:bg-black hover:text-[#F4F4F0] disabled:bg-[#E8E8E3] disabled:text-gray-500 disabled:cursor-wait"
                >
                  <RefreshCcw size={10} className={syncActive ? 'animate-spin' : ''} />
                  {t('courseSync')}
                </button>
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={() => onAnalyzeCourse(course)}
                disabled={busy === 'analysis' || analysisActive || loading}
                className="flex items-center gap-2 px-4 py-2 text-xs font-mono font-bold tracking-widest uppercase border border-black bg-black text-[#F4F4F0] hover:bg-[#F4F4F0] hover:text-black disabled:bg-[#E8E8E3] disabled:text-gray-500 disabled:cursor-wait"
              >
                {busy === 'analysis' || analyzingThisCourse ? <RefreshCcw size={14} className="animate-spin" /> : <Sparkles size={14} />}
                {analyzingThisCourse ? t('analyzing') : t('analyze')}
              </button>
              <button
                onClick={() => setIsHomeExpanded(!isHomeExpanded)}
                className="flex items-center gap-2 px-4 py-2 text-xs font-mono font-bold tracking-widest uppercase border border-black transition-none bg-[#F4F4F0] text-black hover:bg-black hover:text-[#F4F4F0]"
              >
                {isHomeExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                {t('canvasHome')}
              </button>
            </div>
          </div>
          <p className="text-xl text-gray-600 font-medium mb-6">{course.name}</p>
        </div>

        {isHomeExpanded && <CanvasHomePanel course={course} home={detail?.home ?? null} loading={loading} onClose={() => setIsHomeExpanded(false)} />}

        <div className="course-tabs sticky z-20 flex gap-8 border-b border-black mb-8 overflow-x-auto shrink-0 no-scrollbar bg-[#F4F4F0] pt-3">
          {[
            { id: 'timeline' as const, label: t('timeline'), icon: CalendarDays },
            { id: 'files' as const, label: t('dataVault'), icon: FileText },
            { id: 'announcements' as const, label: t('broadcasts'), icon: Bell },
            { id: 'assignments' as const, label: t('assignments'), icon: ClipboardList },
            { id: 'people' as const, label: t('people'), icon: Users }
          ].map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 pb-4 text-xs font-mono font-bold tracking-widest uppercase transition-colors relative whitespace-nowrap ${
                  activeTab === tab.id ? 'text-black' : 'text-gray-400 hover:text-black'
                }`}
              >
                <Icon size={14} />
                {tab.label}
                {activeTab === tab.id && <div className="absolute bottom-[-1px] left-0 w-full h-[2px] bg-black" />}
              </button>
            );
          })}
        </div>

        {loading && <EmptyState>{t('loadingCourseMaterial')}</EmptyState>}
        {!loading && !detail && <EmptyState>{t('noDetailLoaded')}</EmptyState>}
        {!loading && detail && activeTab === 'timeline' && (
          <TimelineTab detail={detail} analyzing={analyzingThisCourse} />
        )}
        {!loading && detail && activeTab === 'files' && (
          <FilesTab course={course} files={detail.files} refreshCourse={refreshCourse} />
        )}
        {!loading && detail && activeTab === 'announcements' && (
          <AnnouncementsTab announcements={detail.announcements} />
        )}
        {!loading && detail && activeTab === 'assignments' && (
          <AssignmentsTab assignments={detail.assignments} />
        )}
        {!loading && detail && activeTab === 'people' && <PeopleTab people={detail.people} />}
      </div>
    </div>
  );
}
