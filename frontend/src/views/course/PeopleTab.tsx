import { Users } from 'lucide-react';
import type { Person } from '../../types';
import { useAppContext } from '../../context/AppContext';
import { Badge, EmptyState } from '../../components/ui';
import { fmtDate } from '../../utils/format';

export function PeopleTab({ people }: { people: Person[] }) {
  const { query, t } = useAppContext();
  const needle = query.trim().toLowerCase();
  const visible = needle ? people.filter((person) => `${person.name} ${person.email ?? ''} ${person.role ?? ''}`.toLowerCase().includes(needle)) : people;
  if (!visible.length) return <EmptyState>{t('noPeople')}</EmptyState>;
  return (
    <div className="border border-black bg-white overflow-hidden">
      <table className="w-full text-left border-collapse">
        <thead>
          <tr className="border-b border-black text-[10px] font-mono tracking-widest text-black bg-[#F4F4F0]">
            <th className="p-4 font-bold">{t('name')}</th>
            <th className="p-4 font-bold">{t('role')}</th>
            <th className="p-4 font-bold">{t('email')}</th>
            <th className="p-4 font-bold text-right">{t('lastActive')}</th>
          </tr>
        </thead>
        <tbody className="text-sm font-medium">
          {visible.map((person, index) => (
            <tr key={person.id} className={`border-black ${index !== visible.length - 1 ? 'border-b' : ''} hover:bg-[#F4F4F0] group`}>
              <td className="p-4">
                <div className="flex items-center gap-3">
                  <Users size={16} className="text-gray-400 group-hover:text-black shrink-0" />
                  <span className="text-black font-bold tracking-tight text-base">{person.name}</span>
                </div>
              </td>
              <td className="p-4">
                <Badge variant={(person.role || '').toLowerCase().includes('teacher') || (person.role || '').toLowerCase().includes('ta') ? 'inverted' : 'default'}>
                  {person.role || t('user')}
                </Badge>
              </td>
              <td className="p-4 text-xs font-mono text-gray-600">{person.email || t('notExposed')}</td>
              <td className="p-4 text-right text-[10px] font-mono text-gray-500 uppercase tracking-widest">{fmtDate(person.last_activity_at, t('noActivity'))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
