import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CheckSquare, ChevronRight, Database, Download, Eye, FileText, Folder, Network, RefreshCcw, Square, X } from 'lucide-react';
import type { Course, MaterialFile } from '../../types';
import { useAppContext } from '../../context/AppContext';
import { backupFile, backupSelectedFiles, downloadSelectedFiles, extractFile, syncCourseFiles } from '../../api/files';
import { Badge, EmptyState } from '../../components/ui';
import { fmtBytes, fmtShortDate } from '../../utils/format';

function segmentsOf(folderPath: string | null | undefined): string[] {
  return (folderPath ?? '')
    .replace(/\\/g, '/')
    .split('/')
    .map((part) => part.trim())
    .filter(Boolean);
}

function buildDirectory(files: MaterialFile[], current: string[]) {
  const folders = new Map<string, number>();
  const filesHere: MaterialFile[] = [];
  for (const file of files) {
    const segs = segmentsOf(file.folder_path);
    if (!current.every((part, index) => segs[index] === part)) continue;
    if (segs.length === current.length) filesHere.push(file);
    else {
      const name = segs[current.length];
      folders.set(name, (folders.get(name) ?? 0) + 1);
    }
  }
  return {
    folders: Array.from(folders.entries())
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => a.name.localeCompare(b.name)),
    filesHere
  };
}

export function FilesTab({
  course,
  files,
  refreshCourse
}: {
  course: Course;
  files: MaterialFile[];
  refreshCourse: () => Promise<void>;
}) {
  const { query, busy, setBusy, setError, t } = useAppContext();
  const [selectedFiles, setSelectedFiles] = useState<Set<number>>(new Set());
  const [previewFile, setPreviewFile] = useState<MaterialFile | null>(null);
  const [currentPath, setCurrentPath] = useState<string[]>([]);
  const needle = query.trim().toLowerCase();
  const visibleFiles = useMemo(
    () => (needle ? files.filter((file) => `${file.display_name} ${file.content_type ?? ''}`.toLowerCase().includes(needle)) : files),
    [files, needle]
  );
  const directory = useMemo(
    () => (needle ? { folders: [], filesHere: visibleFiles } : buildDirectory(visibleFiles, currentPath)),
    [visibleFiles, currentPath, needle]
  );
  const selectedIds = useMemo(() => Array.from(selectedFiles), [selectedFiles]);

  function toggleFile(id: number) {
    setSelectedFiles((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function runFileAction(action: string, handler: () => Promise<void>) {
    setBusy(action);
    setError(null);
    try {
      await handler();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <div className="files-toolbar flex justify-between items-center bg-[#E8E8E3] border border-black p-4 gap-4">
        <span className="files-mount flex items-center gap-2 text-xs font-mono text-gray-600 uppercase font-bold tracking-widest flex-wrap">
          <Database size={14} /> {t('mount')}:
          <button onClick={() => setCurrentPath([])} className="hover:text-black hover:underline underline-offset-4">
            ./data/canvas/course_{course.id}/
          </button>
          {currentPath.map((segment, index) => (
            <Fragment key={index}>
              <span className="text-gray-400">/</span>
              <button
                onClick={() => setCurrentPath(currentPath.slice(0, index + 1))}
                className="hover:text-black hover:underline underline-offset-4 truncate max-w-[12rem]"
              >
                {segment}
              </button>
            </Fragment>
          ))}
        </span>

        <div className="files-actions flex items-center gap-2">
          <button
            onClick={() =>
              runFileAction('backup-selected', async () => {
                await backupSelectedFiles(course.id, selectedIds);
                setSelectedFiles(new Set());
                await refreshCourse();
              })
            }
            disabled={selectedIds.length === 0 || busy !== null}
            className="flex items-center gap-2 px-4 py-2 text-xs font-mono font-bold tracking-widest uppercase border border-black bg-[#F4F4F0] text-black hover:bg-black hover:text-[#F4F4F0] disabled:text-gray-400 disabled:cursor-not-allowed disabled:hover:bg-[#F4F4F0]"
          >
            <RefreshCcw size={14} className={busy === 'backup-selected' ? 'animate-spin' : ''} />
            {t('backupSelected')}
          </button>
          <button
            onClick={() =>
              runFileAction('sync-files', async () => {
                await syncCourseFiles(course.id);
                setSelectedFiles(new Set());
                await refreshCourse();
              })
            }
            disabled={busy !== null}
            className="flex items-center gap-2 px-4 py-2 text-xs font-mono font-bold tracking-widest uppercase border border-black bg-[#F4F4F0] text-black hover:bg-black hover:text-[#F4F4F0] disabled:text-gray-400 disabled:cursor-wait disabled:hover:bg-[#F4F4F0]"
          >
            <RefreshCcw size={14} className={busy === 'sync-files' ? 'animate-spin' : ''} />
            {t('fileSync')}
          </button>
          <button
            onClick={() =>
              runFileAction('download-selected', async () => {
                await downloadSelectedFiles(course.id, selectedIds);
                setSelectedFiles(new Set());
              })
            }
            disabled={selectedIds.length === 0 || busy !== null}
            className="flex items-center gap-2 px-5 py-2 text-xs font-mono font-bold tracking-widest uppercase border border-black transition-none bg-black text-[#F4F4F0] hover:bg-[#F4F4F0] hover:text-black disabled:bg-[#F4F4F0] disabled:text-gray-400 disabled:cursor-not-allowed"
          >
            <Download size={14} className={busy === 'download-selected' ? 'animate-pulse' : ''} />
            {t('batchDownload')} ({selectedIds.length})
          </button>
        </div>
      </div>

      {visibleFiles.length === 0 ? (
        <EmptyState>{t('noFilesMatch')}</EmptyState>
      ) : (
        <div className="border border-black bg-white overflow-hidden">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-black text-[10px] font-mono tracking-widest text-black bg-[#F4F4F0]">
                <th className="p-4 w-12 border-r border-black text-center">{t('sel')}</th>
                <th className="p-4 font-bold">{t('filename')}</th>
                <th className="p-4 font-bold">{t('state')}</th>
                <th className="p-4 font-bold">{t('analysis')}</th>
                <th className="p-4 font-bold text-right">{t('action')}</th>
              </tr>
            </thead>
            <tbody className="text-sm font-medium">
              {directory.folders.map((folder) => (
                <tr key={`dir:${folder.name}`} className="border-b border-black bg-[#E8E8E3] hover:bg-[#DEDED9]">
                  <td colSpan={5} className="p-3 px-4 text-xs font-mono font-bold tracking-widest">
                    <button
                      onClick={() => setCurrentPath([...currentPath, folder.name])}
                      className="w-full flex items-center justify-between gap-3 text-left uppercase"
                    >
                      <span className="flex items-center gap-2 min-w-0">
                        <Folder size={14} />
                        <span className="truncate">{folder.name}</span>
                      </span>
                      <span className="shrink-0 text-gray-500 flex items-center gap-1">
                        {folder.count} {t('filesUnit')} <ChevronRight size={14} />
                      </span>
                    </button>
                  </td>
                </tr>
              ))}
              {directory.filesHere.map((file) => (
                <FileRow
                  key={file.id}
                  course={course}
                  file={file}
                  selected={selectedFiles.has(file.id)}
                  onToggle={() => toggleFile(file.id)}
                  onPreview={() => setPreviewFile(file)}
                  runFileAction={runFileAction}
                  refreshCourse={refreshCourse}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {previewFile && <PreviewModal course={course} file={previewFile} onClose={() => setPreviewFile(null)} />}
    </div>
  );
}

function FileRow({
  course,
  file,
  selected,
  onToggle,
  onPreview,
  runFileAction,
  refreshCourse
}: {
  course: Course;
  file: MaterialFile;
  selected: boolean;
  onToggle: () => void;
  onPreview: () => void;
  runFileAction: (action: string, handler: () => Promise<void>) => Promise<void>;
  refreshCourse: () => Promise<void>;
}) {
  const { busy, t } = useAppContext();
  const isLocal = file.backup_status === 'downloaded';
  const failedDownload = file.backup_status === 'fail_download' || file.backup_status === 'error';
  const fileBusy = busy === `extract-${file.id}` || busy === `backup-${file.id}`;
  return (
    <tr className="border-b border-black last:border-b-0 hover:bg-[#F4F4F0] group">
      <td className="p-4 border-r border-black text-center align-middle">
        <button onClick={onToggle} className="text-black hover:text-gray-500 focus:outline-none" aria-label={`${t('selectFile')} ${file.display_name}`}>
          {selected ? <CheckSquare size={16} /> : <Square size={16} />}
        </button>
      </td>
      <td className="p-4">
        <div className="flex items-center gap-3">
          <FileText size={16} className="text-gray-400 group-hover:text-black shrink-0" />
          <div className="min-w-0">
            <div className="text-black font-bold tracking-tight break-words">{file.display_name}</div>
            <div className="text-[10px] font-mono text-gray-500 mt-1">
              {fmtBytes(file.size)} // {fmtShortDate(file.updated_at)}
            </div>
          </div>
        </div>
      </td>
      <td className="p-4">
        <Badge variant={isLocal ? 'inverted' : failedDownload ? 'warning' : 'default'}>{isLocal ? t('local') : file.backup_status}</Badge>
      </td>
      <td className="p-4">
        <Badge variant={file.extraction_status === 'error' || file.extraction_status === 'unsupported' ? 'warning' : file.extraction_status === 'extracted' ? 'inverted' : 'default'}>
          {file.extraction_status}
        </Badge>
      </td>
      <td className="p-4 text-right">
        <div className="flex justify-end gap-2">
          <button
            onClick={onPreview}
            disabled={!isLocal}
            className={`p-1.5 border border-black transition-none flex items-center justify-center ${
              !isLocal ? 'bg-[#E8E8E3] text-gray-400 cursor-not-allowed' : 'bg-white text-black hover:bg-black hover:text-[#F4F4F0]'
            }`}
            title={t('previewLocalFile')}
          >
            <Eye size={14} />
          </button>
          <a
            href={isLocal ? `/api/courses/${course.id}/files/${file.id}/download` : undefined}
            target={isLocal ? '_blank' : undefined}
            rel={isLocal ? 'noopener noreferrer' : undefined}
            className={`p-1.5 border border-black transition-none flex items-center justify-center ${
              !isLocal ? 'bg-[#E8E8E3] text-gray-400 pointer-events-none' : 'bg-white text-black hover:bg-black hover:text-[#F4F4F0]'
            }`}
            title={t('downloadLocalFile')}
          >
            <Download size={14} />
          </a>
          <button
            onClick={() =>
              runFileAction(`backup-${file.id}`, async () => {
                await backupFile(course.id, file);
                await refreshCourse();
              })
            }
            disabled={fileBusy || busy !== null}
            className="px-3 py-1.5 text-[10px] font-mono font-bold uppercase tracking-widest border border-black transition-none inline-flex items-center gap-2 bg-white text-black hover:bg-black hover:text-[#F4F4F0] disabled:bg-[#E8E8E3] disabled:text-gray-400 disabled:cursor-wait"
          >
            {busy === `backup-${file.id}` ? <RefreshCcw size={12} className="animate-spin" /> : <Download size={12} />}
            {t('fetch')}
          </button>
          <button
            onClick={() =>
              runFileAction(`extract-${file.id}`, async () => {
                await extractFile(course.id, file);
                await refreshCourse();
              })
            }
            disabled={fileBusy || busy !== null || !isLocal}
            className="px-3 py-1.5 text-[10px] font-mono font-bold uppercase tracking-widest border border-black transition-none inline-flex items-center gap-2 bg-white text-black hover:bg-black hover:text-[#F4F4F0] disabled:bg-[#E8E8E3] disabled:text-gray-400 disabled:cursor-not-allowed"
          >
            {busy === `extract-${file.id}` ? <RefreshCcw size={12} className="animate-spin" /> : <Network size={12} />}
            {t('extract')}
          </button>
        </div>
      </td>
    </tr>
  );
}

function PreviewModal({ course, file, onClose }: { course: Course; file: MaterialFile; onClose: () => void }) {
  const { t } = useAppContext();
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const previousHtmlOverflow = document.documentElement.style.overflow;
    const previousBodyOverflow = document.body.style.overflow;
    const previousActiveElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;

    document.documentElement.style.overflow = 'hidden';
    document.body.style.overflow = 'hidden';
    closeButtonRef.current?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.documentElement.style.overflow = previousHtmlOverflow;
      document.body.style.overflow = previousBodyOverflow;
      previousActiveElement?.focus();
    };
  }, [onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-[2147483647] flex items-center justify-center overflow-hidden overscroll-contain bg-black/60 backdrop-blur-sm p-3 sm:p-8"
      role="dialog"
      aria-modal="true"
      aria-label={`${t('previewLocalFile')} ${file.display_name}`}
    >
      <div className="bg-[#F4F4F0] border border-black w-full max-w-6xl h-[calc(100dvh-1.5rem)] sm:h-[calc(100dvh-4rem)] max-h-none min-h-0 flex flex-col shadow-[16px_16px_0_0_rgba(0,0,0,1)]">
        <div className="flex justify-between items-center p-4 border-b border-black bg-black text-[#F4F4F0]">
          <div className="flex items-center gap-3 min-w-0">
            <FileText size={16} className="shrink-0" />
            <span className="font-mono text-sm font-bold tracking-widest uppercase truncate">{file.display_name}</span>
          </div>
          <button
            ref={closeButtonRef}
            onClick={onClose}
            className="font-mono text-xs font-bold hover:underline underline-offset-4 flex items-center gap-1 tracking-widest focus:outline-none focus:ring-2 focus:ring-[#F4F4F0]"
          >
            <X size={14} /> {t('close')}
          </button>
        </div>
        <iframe
          className="min-h-0 flex-1 bg-white"
          src={`/api/courses/${course.id}/files/${file.id}/preview#toolbar=1&navpanes=0&view=FitH`}
          title={`Preview ${file.display_name}`}
        />
      </div>
    </div>,
    document.body
  );
}
