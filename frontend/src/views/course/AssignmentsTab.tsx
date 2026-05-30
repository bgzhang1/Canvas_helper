import { Fragment, KeyboardEvent, useEffect, useState } from 'react';
import { ChevronDown, ChevronUp, ExternalLink } from 'lucide-react';
import type { Assignment } from '../../types';
import { useAppContext } from '../../context/AppContext';
import { SafeHtmlContent } from '../../components/SafeHtmlContent';
import { Badge, EmptyState } from '../../components/ui';
import { fmtDate } from '../../utils/format';

export function AssignmentsTab({
  assignments,
  focusedAssignment
}: {
  assignments: Assignment[];
  focusedAssignment?: { id: number; nonce: number } | null;
}) {
  const { query, t } = useAppContext();
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const needle = query.trim().toLowerCase();
  const visible = needle ? assignments.filter((assignment) => assignment.name.toLowerCase().includes(needle)) : assignments;

  useEffect(() => {
    if (!focusedAssignment) return;
    setExpandedIds((current) => new Set(current).add(focusedAssignment.id));
    window.requestAnimationFrame(() => {
      document.getElementById(`assignment-${focusedAssignment.id}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
  }, [focusedAssignment?.id, focusedAssignment?.nonce]);

  if (!visible.length) return <EmptyState>{t('noAssignments')}</EmptyState>;

  function toggleAssignment(id: number) {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handleRowKeyDown(event: KeyboardEvent<HTMLTableRowElement>, id: number) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    toggleAssignment(id);
  }

  return (
    <div className="border border-black bg-white overflow-hidden">
      <table className="w-full text-left border-collapse">
        <thead>
          <tr className="border-b border-black text-[10px] font-mono tracking-widest text-black bg-[#F4F4F0]">
            <th className="p-4 font-bold">{t('assignmentTitle')}</th>
            <th className="p-4 font-bold">{t('dueDate')}</th>
            <th className="p-4 font-bold">{t('status')}</th>
            <th className="p-4 font-bold text-right">{t('points')}</th>
          </tr>
        </thead>
        <tbody className="text-sm font-medium">
          {visible.map((assignment, index) => {
            const expanded = expandedIds.has(assignment.id);
            const focused = focusedAssignment?.id === assignment.id;
            return (
              <Fragment key={assignment.id}>
                <tr
                  id={`assignment-${assignment.id}`}
                  role="button"
                  tabIndex={0}
                  aria-expanded={expanded}
                  onClick={() => toggleAssignment(assignment.id)}
                  onKeyDown={(event) => handleRowKeyDown(event, assignment.id)}
                  className={`border-black cursor-pointer ${expanded || index !== visible.length - 1 ? 'border-b' : ''} hover:bg-[#F4F4F0] ${
                    focused ? 'outline outline-2 outline-black outline-offset-[-2px]' : ''
                  }`}
                >
                  <td className="p-4">
                    <div className="flex items-center gap-3">
                      <span className="h-7 w-7 shrink-0 border border-black bg-white flex items-center justify-center">
                        {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </span>
                      <div className="min-w-0 text-black font-bold tracking-tight text-base break-words">{assignment.name}</div>
                    </div>
                  </td>
                  <td className="p-4 text-xs font-mono text-gray-600">{fmtDate(assignment.due_at || assignment.lock_at || assignment.unlock_at)}</td>
                  <td className="p-4">
                    <Badge variant={assignment.workflow_state === 'published' ? 'inverted' : 'default'}>{assignment.workflow_state || t('unknown')}</Badge>
                  </td>
                  <td className="p-4 text-right font-mono font-bold tracking-wider">{formatScore(assignment)}</td>
                </tr>
                {expanded && (
                  <tr className={index !== visible.length - 1 ? 'border-b border-black' : ''}>
                    <td colSpan={4} className="bg-[#F4F4F0] p-5">
                      <div className="space-y-5">
                        <div className="grid gap-3 text-xs font-mono sm:grid-cols-2 lg:grid-cols-3">
                          <DetailItem label={t('dueDate')} value={fmtDate(assignment.due_at, '-')} />
                          <DetailItem label={t('unlockDate')} value={fmtDate(assignment.unlock_at, '-')} />
                          <DetailItem label={t('lockDate')} value={fmtDate(assignment.lock_at, '-')} />
                          <DetailItem label={t('assignmentGroup')} value={assignment.assignment_group_name || '-'} />
                          <DetailItem label={t('submissionTypes')} value={formatList(assignment.submission_types)} />
                          <DetailItem label={t('allowedExtensions')} value={formatList(assignment.allowed_extensions)} />
                          <DetailItem label={t('score')} value={formatScore(assignment)} />
                          <DetailItem label={t('submittedDate')} value={fmtDate(assignment.submitted_at, '-')} />
                          <DetailItem label={t('submissionStatus')} value={assignment.submission_workflow_state || '-'} />
                          <DetailItem label={t('updatedDate')} value={fmtDate(assignment.updated_at, '-')} />
                          <DetailItem label={t('createdDate')} value={fmtDate(assignment.created_at, '-')} />
                        </div>

                        <div className="border border-black bg-white p-4">
                          <div className="mb-3 text-[10px] font-mono font-bold uppercase tracking-widest text-gray-500">{t('description')}</div>
                          <SafeHtmlContent html={assignment.description} emptyText={t('noDescription')} />
                        </div>

                        {assignment.html_url && (
                          <a
                            href={assignment.html_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-2 border border-black bg-white px-3 py-2 text-xs font-mono font-bold uppercase tracking-widest hover:bg-black hover:text-[#F4F4F0]"
                          >
                            <ExternalLink size={13} />
                            {t('openInCanvas')}
                          </a>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function DetailItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-black bg-white p-3">
      <div className="mb-1 text-[10px] font-bold uppercase tracking-widest text-gray-500">{label}</div>
      <div className="break-words font-bold">{value}</div>
    </div>
  );
}

function formatList(value: string[] | null | undefined) {
  return value && value.length > 0 ? value.join(', ') : '-';
}

function formatScore(assignment: Assignment) {
  if (assignment.score == null && assignment.points_possible == null) return '-';
  return `${formatScoreValue(assignment.score)} / ${formatScoreValue(assignment.points_possible)}`;
}

function formatScoreValue(value: number | null | undefined) {
  if (value == null) return '-';
  return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/\.?0+$/, '');
}
