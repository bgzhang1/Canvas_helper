import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ActiveTab, AnalysisStatus, AppSettings, Course, CourseDetail, SyncStatus } from '../types';
import { fetchAnalysisStatus } from '../api/analysis';
import { fetchCourseDetail, fetchCourses, startCourseAnalysis, startCourseSync } from '../api/courses';
import { fetchSettings } from '../api/settings';
import { cancelSync, fetchSyncStatus, startGlobalSync } from '../api/sync';
import { idleAnalysisStatus } from '../utils/progress';

/**
 * Owns all Canvas data plus the sync/analysis orchestration that used to live
 * inside the App component: course list, routed course detail, sync/analysis
 * status polling, and the action handlers. The component only consumes the
 * returned state and callbacks, keeping it focused on layout.
 */
export function useCanvasData(selectedCourseId: number | null) {
  const [courses, setCourses] = useState<Course[]>([]);
  const [detail, setDetail] = useState<CourseDetail | null>(null);
  const [activeTab, setActiveTab] = useState<ActiveTab>('timeline');
  const [syncStatus, setSyncStatus] = useState<SyncStatus>({ running: false, run: null });
  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatus>(idleAnalysisStatus);
  const [showAnalysisDone, setShowAnalysisDone] = useState(false);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const [isCancellingSync, setIsCancellingSync] = useState(false);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedCourse = useMemo(
    () => (selectedCourseId != null ? courses.find((course) => course.id === selectedCourseId) ?? null : null),
    [courses, selectedCourseId]
  );
  const selectedCourseRef = useRef<Course | null>(null);
  selectedCourseRef.current = selectedCourse;
  const loadedCourseIdRef = useRef<number | null>(null);
  const detailRequestIdRef = useRef(0);
  const handledAnalysisSuccessRef = useRef<string | null>(null);

  const loadCourses = useCallback(async () => {
    try {
      setCourses(await fetchCourses());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadCourseDetail = useCallback(async (course: Course) => {
    const requestId = ++detailRequestIdRef.current;
    setIsLoadingDetail(true);
    setDetail(null);
    setError(null);
    try {
      const nextDetail = await fetchCourseDetail(course.id);
      if (detailRequestIdRef.current === requestId && selectedCourseRef.current?.id === course.id) {
        setDetail(nextDetail);
      }
    } catch (err) {
      if (detailRequestIdRef.current === requestId && selectedCourseRef.current?.id === course.id) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (detailRequestIdRef.current === requestId) {
        setIsLoadingDetail(false);
      }
    }
  }, []);

  const loadSyncStatus = useCallback(async () => {
    const nextStatus = await fetchSyncStatus();
    setSyncStatus((previous) => {
      const previousActive = previous.running || previous.run?.status === 'running';
      const nextActive = nextStatus.running || nextStatus.run?.status === 'running';
      if (previousActive && !nextActive) {
        void loadCourses();
        const course = selectedCourseRef.current;
        if (course) void loadCourseDetail(course);
      }
      return nextStatus;
    });
  }, [loadCourses, loadCourseDetail]);

  const loadAnalysisStatus = useCallback(async () => {
    const nextStatus = await fetchAnalysisStatus();
    setAnalysisStatus((previous) => {
      const course = selectedCourseRef.current;
      if (nextStatus.running || nextStatus.status === 'running') {
        handledAnalysisSuccessRef.current = null;
      }
      const successKey =
        nextStatus.status === 'succeeded' && nextStatus.course_id != null
          ? `${nextStatus.course_id}:${nextStatus.percent}:${nextStatus.stage}:${nextStatus.message ?? ''}`
          : null;
      const finishedSelectedCourse =
        successKey != null &&
        course != null &&
        nextStatus.course_id === course.id &&
        handledAnalysisSuccessRef.current !== successKey &&
        (previous.running || previous.status !== nextStatus.status || previous.course_id !== nextStatus.course_id);
      if (finishedSelectedCourse) {
        handledAnalysisSuccessRef.current = successKey;
        setShowAnalysisDone(true);
        window.setTimeout(() => setShowAnalysisDone(false), 4500);
        void loadCourseDetail(course);
      }
      return nextStatus;
    });
  }, [loadCourseDetail]);

  const loadSettings = useCallback(async () => {
    try {
      setSettings(await fetchSettings());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void Promise.all([loadCourses(), loadSyncStatus(), loadAnalysisStatus(), loadSettings()]);
  }, [loadCourses, loadSyncStatus, loadAnalysisStatus, loadSettings]);

  const syncActive = isSyncing || syncStatus.running || syncStatus.run?.status === 'running';
  const cancelActive = isCancellingSync || Boolean(syncStatus.cancel_requested);
  const analysisActive = analysisStatus.running || analysisStatus.status === 'running';

  useEffect(() => {
    const timer = window.setInterval(() => loadSyncStatus().catch(() => undefined), syncActive ? 1000 : 5000);
    return () => window.clearInterval(timer);
  }, [syncActive, loadSyncStatus]);

  useEffect(() => {
    const timer = window.setInterval(() => loadAnalysisStatus().catch(() => undefined), analysisActive ? 1000 : 5000);
    return () => window.clearInterval(timer);
  }, [analysisActive, selectedCourseId, loadAnalysisStatus]);

  // Load (or clear) the course detail whenever the routed course changes.
  useEffect(() => {
    if (selectedCourseId == null) {
      setDetail(null);
      loadedCourseIdRef.current = null;
      return;
    }
    if (loadedCourseIdRef.current === selectedCourseId) return;
    const course = courses.find((item) => item.id === selectedCourseId);
    if (!course) return;
    loadedCourseIdRef.current = selectedCourseId;
    setActiveTab('timeline');
    void loadCourseDetail(course);
  }, [selectedCourseId, courses, loadCourseDetail]);

  const handleGlobalSync = useCallback(async () => {
    setIsSyncing(true);
    setError(null);
    try {
      await startGlobalSync();
      await loadSyncStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsSyncing(false);
    }
  }, [loadSyncStatus]);

  const handleCourseSync = useCallback(
    async (course: Course) => {
      setIsSyncing(true);
      setError(null);
      try {
        await startCourseSync(course.id);
        await loadSyncStatus();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setIsSyncing(false);
      }
    },
    [loadSyncStatus]
  );

  const handleAnalyzeCourse = useCallback(
    async (course: Course) => {
      setBusy('analysis');
      setError(null);
      setShowAnalysisDone(false);
      handledAnalysisSuccessRef.current = null;
      setActiveTab('timeline');
      try {
        const started = await startCourseAnalysis(course.id);
        if (started.progress) setAnalysisStatus(started.progress);
        await loadAnalysisStatus();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(null);
      }
    },
    [loadAnalysisStatus]
  );

  const handleCancelSync = useCallback(async () => {
    setIsCancellingSync(true);
    setError(null);
    try {
      await cancelSync();
      await loadSyncStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsCancellingSync(false);
    }
  }, [loadSyncStatus]);

  const refreshSelectedCourse = useCallback(async () => {
    await loadCourses();
    const course = selectedCourseRef.current;
    if (course) await loadCourseDetail(course);
  }, [loadCourses, loadCourseDetail]);

  return {
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
  };
}
