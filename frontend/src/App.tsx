import { useCallback, useEffect, useMemo, useState } from 'react';
import { BookOpen, ChevronDown, ChevronRight, Globe, Menu, MessageSquare, RefreshCcw, Search, Settings2, SquareTerminal, X } from 'lucide-react';
import { Navigate, Route, Routes, useLocation, useMatch, useNavigate } from 'react-router-dom';
import type { Lang, TFunction } from './i18n';
import type { View } from './types';
import { translate } from './i18n';
import { AppContext, type AppContextValue } from './context/AppContext';
import { EmptyState, GridBackground } from './components/ui';
import { SidebarButton } from './components/SidebarButton';
import { SyncProgressBar } from './components/SyncProgressBar';
import { DashboardView } from './views/DashboardView';
import { CourseDetailView } from './views/CourseDetailView';
import { AgentChatView } from './views/AgentChatView';
import { SettingsView } from './views/SettingsView';
import { courseCode, statusForCourse } from './utils/course';
import { analysisStatusToProgress, parseSyncProgress } from './utils/progress';
import { useTermGroups } from './hooks/useTermGroups';
import { useCanvasData } from './hooks/useCanvasData';
import { useAnnouncementsSeen } from './hooks/useAnnouncementsSeen';
import { useExternalLinkHardening } from './hooks/useExternalLinkHardening';

export function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const courseMatch = useMatch('/course/:courseId');
  const selectedCourseId = courseMatch?.params.courseId ? Number(courseMatch.params.courseId) : null;

  const [lang, setLang] = useState<Lang>('zh');
  const [query, setQuery] = useState('');
  const [agentCourseId, setAgentCourseId] = useState<number | null>(null);
  const [mobileSystemOpen, setMobileSystemOpen] = useState(false);
  const [mobileCoursesOpen, setMobileCoursesOpen] = useState(false);
  const t = useCallback<TFunction>((key) => translate(lang, key), [lang]);

  const {
    courses,
    selectedCourse,
    detail,
    activeTab,
    setActiveTab,
    syncStatus,
    analysisStatus,
    showAnalysisDone,
    settings,
    setSettings,
    error,
    setError,
    busy,
    setBusy,
    isLoadingDetail,
    syncActive,
    cancelActive,
    analysisActive,
    loadSettings,
    handleGlobalSync,
    handleCourseSync,
    handleAnalyzeCourse,
    handleCancelSync,
    refreshSelectedCourse
  } = useCanvasData(selectedCourseId);

  const courseGroups = useTermGroups(courses);
  const { seenAnnouncements, markCourseAnnouncementsSeen } = useAnnouncementsSeen(courses);
  useExternalLinkHardening();

  const currentView: View = courseMatch
    ? 'course'
    : location.pathname.startsWith('/agent')
      ? 'agent'
      : location.pathname.startsWith('/settings')
        ? 'settings'
        : 'dashboard';

  const filteredCourses = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return courses;
    return courses.filter((course) => `${course.name} ${course.course_code ?? ''} ${course.term_name ?? ''}`.toLowerCase().includes(needle));
  }, [courses, query]);

  // Viewing a course's announcements clears its "new" dot.
  useEffect(() => {
    if (currentView === 'course' && activeTab === 'announcements' && selectedCourse) {
      markCourseAnnouncementsSeen(selectedCourse.id, selectedCourse.announcement_count);
    }
  }, [currentView, activeTab, selectedCourse, markCourseAnnouncementsSeen]);

  useEffect(() => {
    setMobileSystemOpen(false);
    setMobileCoursesOpen(false);
  }, [location.pathname]);

  const syncProgress = parseSyncProgress(syncStatus);
  const showSyncProgress = syncActive || syncStatus.run?.status === 'failed' || syncStatus.run?.status === 'cancelled';
  const analysisProgress = analysisStatusToProgress(analysisStatus);
  const showAnalysisProgress = analysisActive || showAnalysisDone || analysisStatus.status === 'failed';
  const showSidebarProgress = showSyncProgress || showAnalysisProgress;

  const contextValue = useMemo<AppContextValue>(
    () => ({ lang, setLang, t, canvasBaseUrl: settings?.canvas_base_url ?? '', error, setError, busy, setBusy, query, setQuery }),
    [lang, t, settings?.canvas_base_url, error, setError, busy, setBusy, query]
  );
  const mobileViewLabel =
    currentView === 'dashboard'
      ? t('dashboard')
      : currentView === 'agent'
        ? t('agent')
        : currentView === 'settings'
          ? t('configuration')
          : selectedCourse
            ? courseCode(selectedCourse)
            : t('dashboard');

  function navigateMobile(path: string) {
    setMobileSystemOpen(false);
    setMobileCoursesOpen(false);
    navigate(path);
  }

  return (
    <AppContext.Provider value={contextValue}>
      <div className="app-shell flex h-screen bg-[#F4F4F0] text-black font-sans selection:bg-black selection:text-[#F4F4F0] overflow-hidden relative">
        <GridBackground />

        <div className="mobile-nav hidden border-b border-black bg-[#F4F4F0] z-30 shrink-0">
          <div className="h-14 flex items-center justify-between gap-3 px-4 bg-black text-[#F4F4F0]">
            <div className="flex items-center gap-3 min-w-0 font-mono font-bold tracking-widest">
              <span className="text-lg shrink-0">CW</span>
              <span className="truncate">{t('appTitle')}</span>
            </div>
            <div className="text-[10px] font-mono uppercase tracking-widest truncate">{mobileViewLabel}</div>
          </div>

          <div className="relative grid grid-cols-2 border-t border-black">
            <button
              type="button"
              onClick={() => {
                setMobileSystemOpen((open) => !open);
                setMobileCoursesOpen(false);
              }}
              className="h-11 flex items-center justify-between gap-2 border-r border-black px-4 text-xs font-mono font-bold uppercase tracking-widest bg-[#F4F4F0]"
              aria-expanded={mobileSystemOpen}
            >
              <span className="flex items-center gap-2 min-w-0">
                <Menu size={15} />
                <span className="truncate">{t('sysMenu')}</span>
              </span>
              <ChevronDown size={14} className={mobileSystemOpen ? 'rotate-180' : ''} />
            </button>
            <button
              type="button"
              onClick={() => {
                setMobileCoursesOpen((open) => !open);
                setMobileSystemOpen(false);
              }}
              className="h-11 flex items-center justify-between gap-2 px-4 text-xs font-mono font-bold uppercase tracking-widest bg-[#F4F4F0]"
              aria-expanded={mobileCoursesOpen}
            >
              <span className="flex items-center gap-2 min-w-0">
                <BookOpen size={15} />
                <span className="truncate">{selectedCourse ? courseCode(selectedCourse) : t('activeTerm')}</span>
              </span>
              <ChevronDown size={14} className={mobileCoursesOpen ? 'rotate-180' : ''} />
            </button>

            {mobileSystemOpen && (
              <div className="mobile-dropdown absolute left-0 right-0 top-11 border-t border-b border-black bg-[#F4F4F0] shadow-[0_8px_0_0_rgba(0,0,0,0.16)] z-40">
                <button
                  type="button"
                  onClick={() => navigateMobile('/')}
                  className={`w-full flex items-center gap-3 border-b border-black px-4 py-3 text-left text-sm font-mono ${
                    currentView === 'dashboard' ? 'bg-black text-[#F4F4F0]' : 'bg-[#F4F4F0] text-black'
                  }`}
                >
                  <SquareTerminal size={16} />
                  {t('dashboard')}
                </button>
                <button
                  type="button"
                  onClick={() => navigateMobile('/agent')}
                  className={`w-full flex items-center gap-3 border-b border-black px-4 py-3 text-left text-sm font-mono ${
                    currentView === 'agent' ? 'bg-black text-[#F4F4F0]' : 'bg-[#F4F4F0] text-black'
                  }`}
                >
                  <MessageSquare size={16} />
                  {t('agent')}
                </button>
                <button
                  type="button"
                  onClick={() => navigateMobile('/settings')}
                  className={`w-full flex items-center gap-3 px-4 py-3 text-left text-sm font-mono ${
                    currentView === 'settings' ? 'bg-black text-[#F4F4F0]' : 'bg-[#F4F4F0] text-black'
                  }`}
                >
                  <Settings2 size={16} />
                  {t('configuration')}
                </button>
              </div>
            )}

            {mobileCoursesOpen && (
              <div className="mobile-dropdown absolute left-0 right-0 top-11 max-h-[60vh] overflow-y-auto border-t border-b border-black bg-[#F4F4F0] shadow-[0_8px_0_0_rgba(0,0,0,0.16)] z-40">
                {courseGroups.groups.map((group) => {
                  const open = courseGroups.isExpanded(group.key);
                  const isCurrent = group.key === courseGroups.currentKey;
                  return (
                    <div key={group.key}>
                      <button
                        type="button"
                        onClick={() => courseGroups.toggle(group.key)}
                        aria-expanded={open}
                        className="w-full flex items-center justify-between gap-2 border-b border-black px-4 py-2.5 text-left text-[11px] font-mono font-bold uppercase tracking-widest bg-[#E8E8E3] text-black"
                      >
                        <span className="flex items-center gap-2 min-w-0">
                          {open ? <ChevronDown size={13} className="shrink-0" /> : <ChevronRight size={13} className="shrink-0" />}
                          <span className="truncate">{group.termName ?? t('noTerm')}</span>
                          {isCurrent && <span className="shrink-0 px-1 text-[9px] border border-black">{t('currentTermBadge')}</span>}
                        </span>
                        <span className="shrink-0 text-gray-500">{group.courses.length}</span>
                      </button>
                      {open &&
                        group.courses.map((course) => {
                          const courseStatus = statusForCourse(course);
                          return (
                            <button
                              key={course.id}
                              type="button"
                              onClick={() => navigateMobile(`/course/${course.id}`)}
                              className={`w-full flex items-center justify-between gap-3 border-b border-black px-4 py-3 pl-8 text-left text-sm font-mono ${
                                currentView === 'course' && selectedCourse?.id === course.id ? 'bg-black text-[#F4F4F0]' : 'bg-[#F4F4F0] text-black'
                              }`}
                            >
                              <span className="flex items-center gap-3 min-w-0">
                                <span className={`w-1.5 h-1.5 shrink-0 ${courseStatus === 'SYNCED' ? 'bg-current' : 'bg-gray-400 border border-current'}`} />
                                <span className="truncate">{courseCode(course)}</span>
                              </span>
                              <ChevronRight size={13} className="shrink-0" />
                            </button>
                          );
                        })}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <aside className="app-sidebar w-72 border-r border-black flex flex-col bg-[#F4F4F0] z-20 shrink-0">
          <div className="h-16 flex items-center px-6 border-b border-black bg-black text-[#F4F4F0]">
            <div className="flex items-center gap-3 font-mono font-bold text-lg tracking-widest">
              <span className="text-xl">CW</span>
              <span>{t('appTitle')}</span>
            </div>
          </div>

          <div className="px-4 py-8">
            <div className="text-[10px] font-mono text-gray-500 tracking-widest mb-4 px-2">{t('sysMenu')}</div>
            <nav className="space-y-2">
              <SidebarButton active={currentView === 'dashboard'} icon={<SquareTerminal size={18} strokeWidth={1.5} />} onClick={() => navigate('/')}>
                {t('dashboard')}
              </SidebarButton>
              <SidebarButton active={currentView === 'agent'} icon={<MessageSquare size={18} strokeWidth={1.5} />} onClick={() => navigate('/agent')}>
                {t('agent')}
              </SidebarButton>
              <SidebarButton active={currentView === 'settings'} icon={<Settings2 size={18} strokeWidth={1.5} />} onClick={() => navigate('/settings')}>
                {t('configuration')}
              </SidebarButton>
            </nav>
          </div>

          <div className="px-4 py-4 flex-1 overflow-y-auto">
            <div className="text-[10px] font-mono text-gray-500 tracking-widest mb-4 px-2">{t('activeTerm')}</div>
            <div className="space-y-3">
              {courseGroups.groups.map((group) => {
                const open = courseGroups.isExpanded(group.key);
                const isCurrent = group.key === courseGroups.currentKey;
                return (
                  <div key={group.key}>
                    <button
                      type="button"
                      onClick={() => courseGroups.toggle(group.key)}
                      aria-expanded={open}
                      className="w-full flex items-center justify-between gap-2 px-2 py-1.5 text-[10px] font-mono font-bold tracking-widest text-gray-600 hover:text-black"
                    >
                      <span className="flex items-center gap-1.5 min-w-0">
                        {open ? <ChevronDown size={12} className="shrink-0" /> : <ChevronRight size={12} className="shrink-0" />}
                        <span className="truncate">{group.termName ?? t('noTerm')}</span>
                        {isCurrent && <span className="w-1.5 h-1.5 shrink-0 bg-black rounded-full" title={t('currentTermBadge')} />}
                      </span>
                      <span className="shrink-0 text-gray-400">{group.courses.length}</span>
                    </button>
                    {open && (
                      <div className="space-y-1 mt-1">
                        {group.courses.map((course) => {
                          const courseStatus = statusForCourse(course);
                          return (
                            <button
                              key={course.id}
                              onClick={() => navigate(`/course/${course.id}`)}
                              className={`w-full flex items-center justify-between px-4 py-2 border transition-none text-sm font-mono ${
                                currentView === 'course' && selectedCourse?.id === course.id
                                  ? 'border-black bg-[#E8E8E3] font-bold'
                                  : 'border-transparent hover:border-black'
                              }`}
                            >
                              <div className="flex items-center gap-3 min-w-0">
                                <div className={`w-1.5 h-1.5 shrink-0 ${courseStatus === 'SYNCED' ? 'bg-black' : 'bg-gray-400 border border-black'}`} />
                                <span className="truncate tracking-wider">{courseCode(course)}</span>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {showSidebarProgress && (
            <div className="px-4 py-4 border-t border-black bg-[#F4F4F0] space-y-3">
              {showSyncProgress && <SyncProgressBar progress={syncProgress} active={syncActive} />}
              {showAnalysisProgress && <SyncProgressBar progress={analysisProgress} active={analysisActive} kind="analysis" />}
            </div>
          )}
        </aside>

        <main className="app-main flex-1 min-h-0 min-w-0 flex flex-col h-screen relative overflow-hidden z-10">
          <header className="app-header min-h-16 border-b border-black bg-[#F4F4F0] flex items-center justify-between px-8 shrink-0">
            <div className="flex items-center gap-3 text-xs font-mono tracking-widest text-black min-w-0">
              {currentView === 'dashboard' && <span>~/{t('dashboard')}</span>}
              {currentView === 'agent' && <span>~/{t('agent')}</span>}
              {currentView === 'settings' && <span>~/{t('configuration')}</span>}
              {currentView === 'course' && selectedCourse && (
                <>
                  <button onClick={() => navigate('/')} className="hover:underline underline-offset-4 decoration-1">
                    ~/{t('dashboard')}
                  </button>
                  <ChevronRight size={14} />
                  <span className="font-bold truncate">{courseCode(selectedCourse)}</span>
                </>
              )}
            </div>

            <div className="flex items-center gap-4">
              <button
                onClick={() => setLang(lang === 'en' ? 'zh' : 'en')}
                className="flex items-center gap-2 px-3 py-1.5 text-[10px] font-mono font-bold tracking-widest uppercase border border-black transition-none bg-[#F4F4F0] text-black hover:bg-black hover:text-[#F4F4F0]"
              >
                <Globe size={14} />
                {lang === 'en' ? '中' : 'EN'}
              </button>
              <div className="relative flex items-center border border-black bg-[#F4F4F0]">
                <Search className="absolute left-3 text-black" size={14} />
                <input
                  type="text"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={t('searchPlaceholder')}
                  className="pl-9 pr-4 py-1.5 text-xs font-mono bg-transparent outline-none w-64 placeholder:text-gray-500 focus:bg-[#E8E8E3]"
                />
              </div>
              <button
                onClick={handleGlobalSync}
                disabled={syncActive}
                className={`flex items-center gap-2 px-5 py-1.5 text-xs font-mono font-bold tracking-widest uppercase border border-black transition-none ${
                  syncActive
                    ? 'bg-[#E8E8E3] text-gray-500 cursor-not-allowed'
                    : 'bg-black text-[#F4F4F0] hover:bg-[#F4F4F0] hover:text-black'
                }`}
              >
                <RefreshCcw size={14} className={syncActive ? 'animate-spin' : ''} />
                {syncActive ? t('syncing') : t('forceSync')}
              </button>
              {syncActive && (
                <button
                  onClick={handleCancelSync}
                  disabled={cancelActive}
                  className="flex items-center gap-2 px-4 py-1.5 text-xs font-mono font-bold tracking-widest uppercase border border-black transition-none bg-[#F4F4F0] text-black hover:bg-black hover:text-[#F4F4F0] disabled:bg-[#E8E8E3] disabled:text-gray-500 disabled:cursor-wait"
                >
                  <X size={14} />
                  {cancelActive ? t('cancelling') : t('interruptSync')}
                </button>
              )}
            </div>
          </header>

          {error && (
            <div className="mx-8 mt-4 border border-black bg-white px-4 py-3 text-xs font-mono text-black">
              {t('error')}: {error}
            </div>
          )}

          <div className="app-content flex-1 min-h-0 overflow-y-auto p-12">
            <Routes>
              <Route
                path="/"
                element={<DashboardView courses={filteredCourses} syncStatus={syncStatus} seenAnnouncements={seenAnnouncements} onSelectCourse={(course) => navigate(`/course/${course.id}`)} onSync={handleGlobalSync} syncActive={syncActive} />}
              />
              <Route
                path="/agent"
                element={<AgentChatView courses={courses} selectedCourseId={agentCourseId} setSelectedCourseId={setAgentCourseId} />}
              />
              <Route
                path="/settings"
                element={<SettingsView settings={settings} syncStatus={syncStatus} onSettingsChange={setSettings} onRefreshSettings={loadSettings} />}
              />
              <Route
                path="/course/:courseId"
                element={
                  selectedCourse ? (
                    <CourseDetailView
                      course={selectedCourse}
                      detail={detail}
                      activeTab={activeTab}
                      setActiveTab={setActiveTab}
                      loading={isLoadingDetail}
                      refreshCourse={refreshSelectedCourse}
                      onSyncCourse={handleCourseSync}
                      syncActive={syncActive}
                      onAnalyzeCourse={handleAnalyzeCourse}
                      analysisActive={analysisActive}
                      analysisCourseId={analysisStatus.course_id ?? null}
                    />
                  ) : courses.length > 0 ? (
                    <Navigate to="/" replace />
                  ) : (
                    <EmptyState>{t('loadingCourseMaterial')}</EmptyState>
                  )
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </main>
      </div>
    </AppContext.Provider>
  );
}
