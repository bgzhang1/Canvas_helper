import { type KeyboardEvent, type MouseEvent, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import type { CourseDetail, TimelineItem } from '../../types';
import type { TFunction } from '../../i18n';
import { useAppContext } from '../../context/AppContext';
import { Badge, EmptyState } from '../../components/ui';
import { fmtDate } from '../../utils/format';

export function TimelineTab({
  detail,
  analyzing = false
}: {
  detail: CourseDetail;
  analyzing?: boolean;
}) {
  const { t } = useAppContext();
  const aiItems = detail.timeline.analysis?.timeline;
  const isAiTimeline = Boolean(aiItems?.length);
  const sourceItems = isAiTimeline ? aiItems! : detail.timeline.structured;
  const [isPastExpanded, setIsPastExpanded] = useState(false);
  const [expandedItemKey, setExpandedItemKey] = useState<string | null>(null);
  const [openConfidence, setOpenConfidence] = useState<{ key: string; left: number; top: number; width: number } | null>(null);
  const { items, pastItems, currentItems } = useMemo(() => {
    const threshold = Date.now() - 7 * 24 * 60 * 60 * 1000;
    const itemTime = (item: TimelineItem) => {
      const timestamp = new Date(item.date).getTime();
      return Number.isNaN(timestamp) ? null : timestamp;
    };
    const nextItems = sourceItems
      .slice(0, 100)
      .map((item, index) => ({ item, index, timestamp: itemTime(item) }));
    return {
      items: nextItems,
      pastItems: nextItems
        .filter(({ timestamp }) => timestamp !== null && timestamp < threshold)
        .sort((a, b) => (b.timestamp ?? 0) - (a.timestamp ?? 0)),
      currentItems: nextItems
        .filter(({ timestamp }) => timestamp === null || timestamp >= threshold)
        .sort((a, b) => (a.timestamp ?? Number.MAX_SAFE_INTEGER) - (b.timestamp ?? Number.MAX_SAFE_INTEGER))
    };
  }, [sourceItems]);
  function renderTimelineItem({ item, index }: { item: TimelineItem; index: number }) {
    const itemKey = `${item.title}-${item.date}-${index}`;
    const expanded = expandedItemKey === itemKey;
    const cardClassName = 'w-full text-left border border-black p-6 transition-colors hover:bg-[#E8E8E3] cursor-pointer';
    function toggleCard() {
      setOpenConfidence(null);
      setExpandedItemKey((current) => (current === itemKey ? null : itemKey));
    }
    function handleCardKeyDown(event: KeyboardEvent<HTMLDivElement>) {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      toggleCard();
    }
    function toggleConfidence(event: MouseEvent<HTMLButtonElement>) {
      event.stopPropagation();
      const rect = event.currentTarget.getBoundingClientRect();
      const width = Math.min(288, window.innerWidth - 24);
      const left = Math.min(Math.max(rect.left + rect.width / 2 - width / 2, 12), window.innerWidth - width - 12);
      const below = rect.bottom + 8;
      const top = below + 140 > window.innerHeight ? Math.max(12, rect.top - 148) : below;
      setOpenConfidence((current) => {
        if (current?.key === itemKey) return null;
        return { key: itemKey, left, top, width };
      });
    }
    const content = (
      <>
        <div className="timeline-item-header flex flex-col gap-3 mb-4 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
          <h3 className="min-w-0 text-lg font-bold tracking-tight break-words">{item.title}</h3>
          <div className="flex shrink-0 items-center gap-2 self-start">
            <span className="text-[11px] font-mono bg-black text-[#F4F4F0] px-2 py-1">{fmtDate(item.date)}</span>
            {expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          </div>
        </div>
        <p className="text-sm text-gray-600 mb-6 font-medium">
          {t('source')}: {item.source}
        </p>
        <div className="flex flex-wrap gap-3">
          <Badge>{item.source}</Badge>
          {analyzing && <Badge variant="warning">{t('analyzing')}</Badge>}
          {isAiTimeline && item.confidence && (
            <span className="relative inline-flex">
              <button
                type="button"
                onClick={toggleConfidence}
                className={`px-2 py-0.5 text-[10px] font-mono tracking-widest uppercase border ${
                  confidenceVariantClass(item.confidence)
                }`}
                aria-expanded={openConfidence?.key === itemKey}
              >
                {t('confidence')}: {item.confidence}
              </button>
              {openConfidence?.key === itemKey && (
                <div
                  className="fixed z-[2147483647] border border-black bg-white p-4 text-left text-black shadow-[6px_6px_0_0_rgba(0,0,0,1)]"
                  style={{ left: openConfidence.left, top: openConfidence.top, width: openConfidence.width }}
                  onClick={(event) => event.stopPropagation()}
                >
                  <div className="mb-2 text-[10px] font-mono font-bold uppercase tracking-widest text-gray-500">{t('confidenceReason')}</div>
                  <p className="text-xs font-medium leading-relaxed normal-case tracking-normal">{confidenceReason(item, t)}</p>
                </div>
              )}
            </span>
          )}
        </div>
        {expanded && (
          <div className="mt-5 border-t border-black pt-5" onClick={(event) => event.stopPropagation()}>
            <div className="grid gap-3 text-xs font-mono sm:grid-cols-2 lg:grid-cols-4">
              <TimelineDetail label="DATE" value={fmtDate(item.date)} />
              <TimelineDetail label={t('source')} value={item.source} />
              {item.item_id != null && <TimelineDetail label="ITEM_ID" value={String(item.item_id)} />}
              {item.confidence && <TimelineDetail label={t('confidence')} value={item.confidence} />}
            </div>
            {(isAiTimeline || item.confidence_reason) && (
              <div className="mt-4 border border-black bg-white p-4 text-black">
                <div className="mb-2 text-[10px] font-mono font-bold uppercase tracking-widest text-gray-500">{t('confidenceReason')}</div>
                <p className="text-xs font-medium leading-relaxed">{confidenceReason(item, t)}</p>
              </div>
            )}
          </div>
        )}
      </>
    );
    return (
      <div key={`${item.title}-${item.date}-${index}`} className="relative pl-8">
        <div className={`absolute -left-[6px] top-1.5 w-3 h-3 border border-black bg-[#F4F4F0] ${item.source === 'assignment' || item.source === 'calendar' ? 'bg-black' : ''}`} />
        <div
          role="button"
          tabIndex={0}
          onClick={toggleCard}
          onKeyDown={handleCardKeyDown}
          className={cardClassName}
          aria-expanded={expanded}
        >
          {content}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-12" onClick={() => setOpenConfidence(null)}>
      {items.length === 0 ? (
        <EmptyState>{t('noSyncedDates')}</EmptyState>
      ) : (
        <div className="relative border-l border-black ml-4 py-4 space-y-12">
          {currentItems.map(renderTimelineItem)}
          {pastItems.length > 0 && (
            <div className="relative pl-8">
              <button
                onClick={() => setIsPastExpanded(!isPastExpanded)}
                className="inline-flex items-center gap-2 px-4 py-2 border border-black bg-[#F4F4F0] hover:bg-black hover:text-[#F4F4F0] text-xs font-mono font-bold uppercase tracking-widest"
              >
                {isPastExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                {t('pastEvents')} ({pastItems.length})
              </button>
            </div>
          )}
          {isPastExpanded && pastItems.map(renderTimelineItem)}
        </div>
      )}
    </div>
  );
}

function TimelineDetail({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-black bg-white p-3">
      <div className="mb-1 text-[10px] font-bold uppercase tracking-widest text-gray-500">{label}</div>
      <div className="break-words font-bold">{value}</div>
    </div>
  );
}

function confidenceVariantClass(value: string) {
  const normalized = value.toLowerCase();
  if (normalized === 'high') return 'border-black bg-black text-[#F4F4F0]';
  if (normalized === 'low') return 'border-black bg-white text-black underline decoration-2';
  return 'border-black bg-[#F4F4F0] text-black decoration-wavy underline decoration-1';
}

function confidenceReason(item: TimelineItem, t: TFunction) {
  if (item.confidence_reason?.trim()) return item.confidence_reason.trim();
  const normalized = item.confidence?.toLowerCase();
  if (normalized === 'high') return t('confidenceHighReason');
  if (normalized === 'low') return t('confidenceLowReason');
  return t('confidenceMediumReason');
}
