import { useEffect, useState } from 'react';
import { ChevronDown, ChevronUp, ExternalLink } from 'lucide-react';
import type { Announcement } from '../../types';
import { useAppContext } from '../../context/AppContext';
import { EmptyState } from '../../components/ui';
import { SafeHtmlContent } from '../../components/SafeHtmlContent';
import { fmtShortDate } from '../../utils/format';

export function AnnouncementsTab({
  announcements,
  focusedAnnouncement
}: {
  announcements: Announcement[];
  focusedAnnouncement?: { id: number; nonce: number } | null;
}) {
  const { query, t } = useAppContext();
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const needle = query.trim().toLowerCase();
  const visible = needle ? announcements.filter((ann) => `${ann.title} ${ann.message ?? ''}`.toLowerCase().includes(needle)) : announcements;

  useEffect(() => {
    if (!focusedAnnouncement) return;
    setExpandedIds((current) => new Set(current).add(focusedAnnouncement.id));
    window.requestAnimationFrame(() => {
      document.getElementById(`announcement-${focusedAnnouncement.id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
  }, [focusedAnnouncement?.id, focusedAnnouncement?.nonce]);

  if (!visible.length) return <EmptyState>{t('noBroadcasts')}</EmptyState>;

  function toggleAnnouncement(id: number) {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="border border-black bg-[#F4F4F0]">
      {visible.map((ann, index) => {
        const expanded = expandedIds.has(ann.id);
        const focused = focusedAnnouncement?.id === ann.id;
        return (
          <div
            key={ann.id}
            id={`announcement-${ann.id}`}
            className={`${index !== visible.length - 1 ? 'border-b border-black' : ''} ${focused ? 'outline outline-2 outline-black outline-offset-[-2px]' : ''}`}
          >
            <button
              type="button"
              onClick={() => toggleAnnouncement(ann.id)}
              aria-expanded={expanded}
              className="w-full flex items-start justify-between gap-4 p-5 text-left hover:bg-[#E8E8E3] transition-colors"
            >
              <div className="min-w-0">
                <h3 className="text-lg font-bold tracking-tight uppercase break-words">{ann.title}</h3>
                <div className="mt-2 text-[10px] font-mono text-gray-500 tracking-widest uppercase">{t('author')}: {ann.author_name || t('canvas')}</div>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <span className="text-[11px] font-mono bg-black text-[#F4F4F0] px-2 py-1">{fmtShortDate(ann.posted_at)}</span>
                <span className="h-7 w-7 border border-black bg-white flex items-center justify-center">
                  {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </span>
              </div>
            </button>
            {expanded && (
              <div className="border-t border-black bg-white p-5 space-y-4">
                <SafeHtmlContent html={ann.message} emptyText={t('noDescription')} />
                {ann.html_url && (
                  <a
                    href={ann.html_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-2 border border-black px-3 py-2 text-xs font-mono font-bold uppercase tracking-widest hover:bg-black hover:text-[#F4F4F0]"
                  >
                    <ExternalLink size={13} />
                    {t('openInCanvas')}
                  </a>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
