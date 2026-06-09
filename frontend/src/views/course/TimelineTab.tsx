import { type KeyboardEvent, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import type { CourseDetail, TimelineItem } from '../../types';
import { useAppContext } from '../../context/AppContext';
import { Badge, EmptyState } from '../../components/ui';
import { fmtDate } from '../../utils/format';

export function TimelineTab({ detail }: { detail: CourseDetail }) {
  const { t } = useAppContext();
  const sourceItems = detail.timeline.structured;
  const [isPastExpanded, setIsPastExpanded] = useState(false);
  const [expandedItemKey, setExpandedItemKey] = useState<string | null>(null);
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
      setExpandedItemKey((current) => (current === itemKey ? null : itemKey));
    }
    function handleCardKeyDown(event: KeyboardEvent<HTMLDivElement>) {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      toggleCard();
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
        </div>
        {expanded && (
          <div className="mt-5 border-t border-black pt-5" onClick={(event) => event.stopPropagation()}>
            <div className="grid gap-3 text-xs font-mono sm:grid-cols-2 lg:grid-cols-4">
              <TimelineDetail label="DATE" value={fmtDate(item.date)} />
              <TimelineDetail label={t('source')} value={item.source} />
              {item.item_id != null && <TimelineDetail label="ITEM_ID" value={String(item.item_id)} />}
            </div>
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
    <div className="space-y-12">
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
