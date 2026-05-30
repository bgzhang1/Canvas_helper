import type { ReactNode } from 'react';
import { ChevronDown, ChevronUp, RefreshCcw, Save } from 'lucide-react';
import type { EventLog, EventLogFilter } from '../types';
import { useAppContext } from '../context/AppContext';
import { Badge, EmptyState } from './ui';
import { fmtDate } from '../utils/format';
import {
  eventActionLabel,
  eventCategoryLabel,
  eventLogBadgeVariant,
  eventLogLevel,
  eventStatusLabel
} from '../utils/labels';

export function EventLogList({ events, filter }: { events: EventLog[]; filter: EventLogFilter }) {
  const { t } = useAppContext();
  if (events.length === 0) return <EmptyState>{t('noLogs')}</EmptyState>;
  const visibleEvents = filter === 'all' ? events : events.filter((event) => eventLogLevel(event.status) === filter);
  if (visibleEvents.length === 0) return <EmptyState>{t('noLogsForFilter')}</EmptyState>;
  return (
    <div className="border border-black bg-white max-h-[32rem] overflow-y-auto">
      {visibleEvents.map((event, index) => {
        return (
          <div key={event.id} className={`p-4 ${index !== visibleEvents.length - 1 ? 'border-b border-black' : ''}`}>
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <Badge variant={eventLogBadgeVariant(event.status)}>{eventStatusLabel(event.status, t)}</Badge>
              <Badge>{eventCategoryLabel(event.category, t)}</Badge>
              <span className="text-[10px] font-mono text-gray-500 uppercase tracking-widest">{fmtDate(event.created_at)}</span>
            </div>
            <div className="text-sm font-bold break-words">{eventActionLabel(event.action, t)}</div>
            <div className="mt-1 text-xs font-mono text-gray-600 break-words">
              {event.course_name && <span>{event.course_name}</span>}
              {event.item_name && <span>{event.course_name ? ' // ' : ''}{event.item_name}</span>}
              {!event.course_name && !event.item_name && <span>{event.message || eventActionLabel(event.action, t)}</span>}
            </div>
            {event.message && <div className="mt-2 text-xs font-mono text-gray-700 break-words">{event.message}</div>}
          </div>
        );
      })}
    </div>
  );
}

export function AccordionHeader({ expanded, icon, label, onClick }: { expanded: boolean; icon: ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="w-full flex items-center justify-between text-xs font-mono font-bold tracking-widest uppercase mb-2 p-2 hover:bg-black hover:text-[#F4F4F0] transition-colors border border-transparent hover:border-black group"
    >
      <span className="flex items-center gap-2 text-gray-500 group-hover:text-[#F4F4F0]">
        {icon}
        {label}
      </span>
      {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
    </button>
  );
}

export function ToggleSwitch({ checked, onChange }: { checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="relative inline-flex items-center cursor-pointer shrink-0">
      <input type="checkbox" className="sr-only peer" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <div className="w-12 h-6 border-2 border-black bg-[#F4F4F0] peer-focus:outline-none peer-checked:after:translate-x-6 peer-checked:after:border-black after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-black after:border-black after:border after:h-4 after:w-4 after:transition-all peer-checked:bg-[#F4F4F0]"></div>
    </label>
  );
}

export function TextField({
  label,
  value,
  onChange,
  placeholder,
  type = 'text'
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: 'text' | 'password' | 'email';
}) {
  return (
    <div className="flex flex-col gap-2">
      <label className="text-[10px] font-mono tracking-widest uppercase">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="border border-black px-4 py-2 text-sm font-mono bg-white focus:bg-[#E8E8E3] outline-none transition-colors"
      />
    </div>
  );
}

export function SaveConfigButton({ saving, label, onClick }: { saving: boolean; label: string; onClick: () => void }) {
  return (
    <div className="flex justify-end min-w-0">
      <button
        onClick={onClick}
        disabled={saving}
        className="flex max-w-full items-center justify-center gap-2 px-5 py-2 text-xs font-mono font-bold tracking-widest uppercase border border-black bg-black text-[#F4F4F0] hover:bg-[#F4F4F0] hover:text-black disabled:bg-[#E8E8E3] disabled:text-gray-500"
      >
        {saving ? <RefreshCcw size={14} className="animate-spin" /> : <Save size={14} />}
        {label}
      </button>
    </div>
  );
}

export function ConfigRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-col gap-2 min-w-0">
      <label className="text-[10px] font-mono tracking-widest">{label}</label>
      <div className="config-value border border-black px-4 py-2 text-sm font-mono bg-[#E8E8E3] break-words">{value}</div>
    </div>
  );
}
