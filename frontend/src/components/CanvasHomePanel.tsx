import { X } from 'lucide-react';
import type { Course, CourseHome } from '../types';
import { useAppContext } from '../context/AppContext';
import { courseCode } from '../utils/course';

export function CanvasHomePanel({ course, home, loading, onClose }: { course: Course; home: CourseHome | null; loading: boolean; onClose: () => void }) {
  const { t } = useAppContext();
  const srcDoc = `<!doctype html><html><head><base target="_blank"><style>
body{font-family:Inter,Arial,sans-serif;line-height:1.55;color:#111;margin:0;padding:28px;background:#fff}
a{color:#111;text-decoration:underline} img,video,iframe{max-width:100%;height:auto}
table{border-collapse:collapse;max-width:100%}td,th{border:1px solid #111;padding:6px}
</style></head><body>${home?.body || `<h2>${t('noCanvasHomeHeading')} ${courseCode(course)}</h2><p>${t('noCanvasHomeBody')}</p>`}</body></html>`;
  return (
    <div className="canvas-home-panel mb-8 h-[min(70vh,42rem)] min-h-80 border border-black bg-white overflow-hidden shadow-[8px_8px_0_0_#111] flex flex-col">
      <div className="border-b border-black bg-[#E8E8E3] px-4 py-2 flex flex-wrap items-center justify-between gap-2">
        <span className="min-w-0 text-[10px] font-mono text-gray-600 truncate">{t('document')}: {home?.title || t('frontPageHtml')}</span>
        <div className="flex items-center gap-3 shrink-0">
          <span className="text-[10px] font-mono text-gray-600">{t('mountStatus')}: {loading ? t('loading') : home ? t('cached') : t('empty')}</span>
          <button onClick={onClose} className="p-1 border border-black bg-white hover:bg-black hover:text-[#F4F4F0]" aria-label={t('closeCanvasHome')}>
            <X size={12} />
          </button>
        </div>
      </div>
      <iframe className="canvas-home-frame w-full flex-1 bg-white" srcDoc={srcDoc} sandbox="allow-popups allow-popups-to-escape-sandbox" title={`${courseCode(course)} Canvas home`} />
    </div>
  );
}
