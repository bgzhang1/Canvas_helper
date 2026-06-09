import type { SyncProgress } from '../types';
import { useAppContext } from '../context/AppContext';
import { syncStageLabel } from '../utils/labels';

export function SyncProgressBar({ progress, active }: { progress: SyncProgress; active: boolean }) {
  const { t } = useAppContext();
  const percent = Math.max(0, Math.min(100, Math.round(progress.percent || 0)));
  const prefix = active
    ? t('syncRunning')
    : progress.status === 'failed'
      ? t('syncFailed')
      : progress.status === 'cancelled'
        ? t('syncCancelled')
        : t('syncStatus');

  return (
    <div className="border border-black bg-[#F4F4F0] px-4 py-3">
      <div className="flex items-center justify-between gap-4 text-[10px] font-mono font-bold tracking-widest uppercase mb-2">
        <span className="truncate">
          {prefix} // {syncStageLabel(progress.stage, t)}
        </span>
        <span className="shrink-0">{percent}%</span>
      </div>
      <div className="h-3 border border-black bg-white overflow-hidden">
        <div className={`h-full bg-black transition-all duration-300 ${active ? 'animate-pulse' : ''}`} style={{ width: `${percent}%` }} />
      </div>
      {(progress.course || (progress.total ?? 0) > 0) && (
        <div className="mt-2 text-[10px] font-mono text-gray-500 uppercase truncate">
          {progress.total ? `${progress.current ?? 0}/${progress.total}` : ''}
          {progress.course ? ` // ${progress.course}` : ''}
          {progress.file ? ` // ${progress.file}` : ''}
        </div>
      )}
    </div>
  );
}
