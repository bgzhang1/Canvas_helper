import { useEffect, useMemo, useState } from 'react';
import { Bell, Cpu, Database, FileText, Network, RefreshCcw, ShieldCheck } from 'lucide-react';
import type { AppSettings, CanvasTestResult, EventLog, EventLogFilter, SyncStatus } from '../types';
import { useAppContext } from '../context/AppContext';
import {
  fetchEventLogs,
  saveAISettings as persistAISettings,
  saveCanvasSettings as persistCanvasSettings,
  saveNotificationSettings as persistNotificationSettings,
  saveSyncSettings as persistSyncSettings,
  testCanvasSettings as requestCanvasSettingsTest
} from '../api/settings';
import { EmptyState } from '../components/ui';
import { AccordionHeader, ConfigRow, EventLogList, SaveConfigButton, TextField, ToggleSwitch } from '../components/settings';
import { eventLogFilterLabel, eventLogLevel, syncStatusLabel } from '../utils/labels';

export function SettingsView({
  settings,
  syncStatus,
  onSettingsChange,
  onRefreshSettings
}: {
  settings: AppSettings | null;
  syncStatus: SyncStatus;
  onSettingsChange: (settings: AppSettings) => void;
  onRefreshSettings: () => Promise<void>;
}) {
  const { setError, t } = useAppContext();
  const [saving, setSaving] = useState<'canvas' | 'sync' | 'ai' | 'notifications' | null>(null);
  const [expanded, setExpanded] = useState({
    canvas: true,
    ai: false,
    daemon: false,
    push: false,
    logs: false
  });
  const [canvasApiToken, setCanvasApiToken] = useState('');
  const [aiApiKey, setAiApiKey] = useState('');
  const [telegramBotToken, setTelegramBotToken] = useState('');
  const [isTestingCanvas, setIsTestingCanvas] = useState(false);
  const [canvasTest, setCanvasTest] = useState<CanvasTestResult | null>(null);
  const [eventLogs, setEventLogs] = useState<EventLog[]>([]);
  const [isLoadingEvents, setIsLoadingEvents] = useState(false);
  const [eventLogFilter, setEventLogFilter] = useState<EventLogFilter>('all');
  const eventLogCounts = useMemo(
    () =>
      eventLogs.reduce(
        (counts, event) => {
          counts.all += 1;
          const level = eventLogLevel(event.status);
          if (level === 'success' || level === 'failed' || level === 'warning') {
            counts[level] += 1;
          }
          return counts;
        },
        { all: 0, success: 0, failed: 0, warning: 0 } as Record<EventLogFilter, number>
      ),
    [eventLogs]
  );

  useEffect(() => {
    loadEventLogs().catch(() => undefined);
  }, []);

  function toggleSection(section: keyof typeof expanded) {
    setExpanded((current) => ({ ...current, [section]: !current[section] }));
  }

  function updateAI(values: Partial<AppSettings['ai']>) {
    if (!settings) return;
    onSettingsChange({ ...settings, ai: { ...settings.ai, ...values } });
  }

  function updateNotifications(values: Partial<AppSettings['notifications']>) {
    if (!settings) return;
    onSettingsChange({ ...settings, notifications: { ...settings.notifications, ...values } });
  }

  async function loadEventLogs() {
    setIsLoadingEvents(true);
    try {
      setEventLogs(await fetchEventLogs());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsLoadingEvents(false);
    }
  }

  async function saveCanvasSettings() {
    if (!settings) return;
    setSaving('canvas');
    setError(null);
    try {
      const canvas = await persistCanvasSettings(canvasApiToken);
      setCanvasApiToken('');
      onSettingsChange({
        ...settings,
        canvas_base_url: canvas.canvas_base_url,
        token_configured: canvas.token_configured
      });
      await onRefreshSettings();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  }

  async function testCanvasSettings() {
    setIsTestingCanvas(true);
    setCanvasTest(null);
    setError(null);
    try {
      setCanvasTest(await requestCanvasSettingsTest(canvasApiToken));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsTestingCanvas(false);
    }
  }

  async function saveSyncSettings() {
    if (!settings) return;
    setSaving('sync');
    setError(null);
    try {
      const sync = await persistSyncSettings(settings.sync);
      onSettingsChange({ ...settings, sync });
      await onRefreshSettings();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  }

  async function saveAISettings() {
    if (!settings) return;
    setSaving('ai');
    setError(null);
    try {
      const ai = await persistAISettings(settings.ai, aiApiKey);
      setAiApiKey('');
      onSettingsChange({ ...settings, ai });
      await onRefreshSettings();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  }

  async function saveNotificationSettings() {
    if (!settings) return;
    setSaving('notifications');
    setError(null);
    try {
      const notifications = await persistNotificationSettings(settings.notifications, telegramBotToken);
      setTelegramBotToken('');
      onSettingsChange({ ...settings, notifications });
      await onRefreshSettings();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="settings-root max-w-4xl mx-auto pb-12">
      <h1 className="text-5xl font-bold tracking-tighter mb-12 border-b border-black pb-6 uppercase">{t('configTitle')}</h1>

      {!settings ? (
        <EmptyState>{t('loadingConfig')}</EmptyState>
      ) : (
        <div className="grid gap-6">
          <section>
            <AccordionHeader expanded={expanded.canvas} icon={<Database size={14} />} label={t('securityEnv')} onClick={() => toggleSection('canvas')} />
            {expanded.canvas && (
              <div className="settings-panel border border-black bg-[#F4F4F0] p-6 space-y-6">
                <ConfigRow label={t('canvasBaseUrl')} value={settings.canvas_base_url} />
                <div className="flex flex-col gap-2">
                  <label className="text-[10px] font-mono tracking-widest uppercase">{t('canvasApiToken')}</label>
                  <div className="border border-black px-4 py-2 text-sm font-mono bg-black text-[#F4F4F0] flex justify-between items-center gap-4">
                    <span className="truncate">{settings.token_configured ? '****************************************' : t('notConfigured')}</span>
                    <span className="text-[10px] bg-[#F4F4F0] text-black px-1 uppercase whitespace-nowrap">{t('isolated')}</span>
                  </div>
                  <p className="text-[10px] font-mono text-gray-500 mt-1 uppercase">{t('tokenNotice')}</p>
                </div>
                <TextField
                  label={t('newCanvasApiToken')}
                  value={canvasApiToken}
                  onChange={setCanvasApiToken}
                  placeholder={t('canvasTokenPlaceholder')}
                  type="password"
                />
                <div className="settings-actions flex flex-col sm:flex-row gap-3">
                  <SaveConfigButton saving={saving === 'canvas'} label={t('saveConfig')} onClick={saveCanvasSettings} />
                  <button
                    onClick={testCanvasSettings}
                    disabled={isTestingCanvas}
                    className="flex min-w-0 max-w-full items-center justify-center gap-2 px-4 py-2 text-xs font-mono font-bold tracking-widest uppercase border border-black bg-[#F4F4F0] text-black hover:bg-black hover:text-[#F4F4F0] disabled:bg-[#E8E8E3] disabled:text-gray-500 disabled:cursor-wait"
                  >
                    <Network size={14} className={isTestingCanvas ? 'animate-pulse' : ''} />
                    {isTestingCanvas ? t('testing') : t('testCanvas')}
                  </button>
                </div>
                {canvasTest && (
                  <div className={`border border-black px-4 py-3 text-xs font-mono ${canvasTest.ok ? 'bg-black text-[#F4F4F0]' : 'bg-white text-black'}`}>
                    <div className="font-bold tracking-widest uppercase">{canvasTest.ok ? t('testPassed') : t('testFailed')}</div>
                    <div className="mt-1 break-words">{canvasTest.ok ? t('canvasTestOkMessage') : canvasTest.message}</div>
                    {canvasTest.username && (
                      <div className="mt-1 break-words">
                        {t('currentUser')}: {canvasTest.username}
                      </div>
                    )}
                  </div>
                )}
                <ConfigRow label={t('ocr')} value={`${settings.ocr.enabled ? t('enabled') : t('disabled')} // ${settings.ocr.languages} // ${settings.ocr.max_pages} ${t('pages')}`} />
              </div>
            )}
          </section>

          <section>
            <AccordionHeader expanded={expanded.ai} icon={<Cpu size={14} />} label={t('aiInference')} onClick={() => toggleSection('ai')} />
            {expanded.ai && (
              <div className="settings-panel border border-black bg-[#F4F4F0] p-6 space-y-6">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <TextField label={t('compatBaseUrl')} value={settings.ai.base_url} onChange={(value) => updateAI({ base_url: value })} placeholder="https://api.openai.com/v1" />
                  <TextField
                    label={t('apiKey')}
                    value={aiApiKey}
                    onChange={setAiApiKey}
                    placeholder={settings.ai.api_key_configured ? t('existingKeyPlaceholder') : 'sk-...'}
                    type="password"
                  />
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  <TextField label={t('modelSelect')} value={settings.ai.model} onChange={(value) => updateAI({ model: value })} placeholder={t('modelPlaceholder')} />
                  <div className="flex flex-col gap-2">
                    <label className="text-[10px] font-mono tracking-widest uppercase">{t('reasoningEffort')}</label>
                    <select
                      value={settings.ai.reasoning_effort}
                      onChange={(event) => updateAI({ reasoning_effort: event.target.value })}
                      className="border border-black bg-white focus:bg-[#E8E8E3] text-sm font-mono rounded-none focus:ring-0 outline-none p-2 uppercase"
                    >
                      <option value="low">{t('low')}</option>
                      <option value="medium">{t('medium')}</option>
                      <option value="high">{t('high')}</option>
                    </select>
                  </div>
                </div>

                <div className="flex flex-col gap-2">
                  <label className="text-[10px] font-mono tracking-widest uppercase">{t('skillManagement')}</label>
                  <textarea
                    rows={3}
                    value={settings.ai.skills}
                    onChange={(event) => updateAI({ skills: event.target.value })}
                    placeholder={t('skillPlaceholder')}
                    className="border border-black px-4 py-3 text-sm font-mono bg-white focus:bg-[#E8E8E3] outline-none transition-colors resize-y min-h-[80px]"
                  />
                </div>

                <SaveConfigButton saving={saving === 'ai'} label={t('saveConfig')} onClick={saveAISettings} />
              </div>
            )}
          </section>

          <section>
            <AccordionHeader expanded={expanded.daemon} icon={<RefreshCcw size={14} />} label={t('syncDaemon')} onClick={() => toggleSection('daemon')} />
            {expanded.daemon && (
              <div className="settings-panel border border-black bg-[#F4F4F0] p-6 space-y-8">
                <div className="flex items-center justify-between border-b border-black pb-6 gap-4">
                  <div>
                    <div className="font-bold text-sm uppercase tracking-wider">{t('backgroundFetch')}</div>
                    <div className="text-xs text-gray-500 mt-1 font-mono uppercase">{t('backgroundFetchDesc')}</div>
                  </div>
                  <ToggleSwitch checked={settings.sync.enabled} onChange={(checked) => onSettingsChange({ ...settings, sync: { ...settings.sync, enabled: checked } })} />
                </div>

                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                  <div>
                    <div className="font-bold text-sm uppercase tracking-wider">{t('cronInterval')}</div>
                    <div className="text-xs text-gray-500 mt-1 font-mono uppercase">
                      {t('latest')}: {syncStatusLabel(syncStatus.run?.status, t)}
                    </div>
                  </div>
                  <select
                    value={settings.sync.interval_minutes}
                    onChange={(event) => onSettingsChange({ ...settings, sync: { ...settings.sync, interval_minutes: Number(event.target.value) } })}
                    className="border border-black bg-white focus:bg-[#E8E8E3] text-sm font-mono rounded-none focus:ring-0 outline-none p-2 uppercase shrink-0"
                  >
                    <option value={15}>{t('every15min')}</option>
                    <option value={60}>{t('every1h')}</option>
                    <option value={360}>{t('every6h')}</option>
                    <option value={1440}>{t('nightly')}</option>
                  </select>
                </div>

                <SaveConfigButton saving={saving === 'sync'} label={t('saveConfig')} onClick={saveSyncSettings} />
              </div>
            )}
          </section>

          <section>
            <AccordionHeader expanded={expanded.push} icon={<Bell size={14} />} label={t('notificationChannels')} onClick={() => toggleSection('push')} />
            {expanded.push && (
              <div className="settings-panel border border-black bg-[#F4F4F0] p-6 space-y-8">
                <div>
                  <div className="flex items-center justify-between border-b border-black pb-4 gap-4 mb-4">
                    <div>
                      <div className="font-bold text-sm uppercase tracking-wider">{t('telegramPush')}</div>
                      <div className="text-xs text-gray-500 mt-1 font-mono uppercase">{t('telegramDesc')}</div>
                    </div>
                    <ToggleSwitch checked={settings.notifications.telegram_enabled} onChange={(checked) => updateNotifications({ telegram_enabled: checked })} />
                  </div>
                  {settings.notifications.telegram_enabled && (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6 p-4 border border-black bg-[#E8E8E3]">
                      <TextField
                        label={t('botToken')}
                        value={telegramBotToken}
                        onChange={setTelegramBotToken}
                        placeholder={settings.notifications.telegram_configured ? t('existingKeyPlaceholder') : '123456:ABC...'}
                        type="password"
                      />
                      <TextField label={t('chatId')} value={settings.notifications.telegram_chat_id} onChange={(value) => updateNotifications({ telegram_chat_id: value })} placeholder="@channel or 123456789" />
                    </div>
                  )}
                </div>

                <div>
                  <div className="flex items-center justify-between border-b border-black pb-4 gap-4 mb-4">
                    <div>
                      <div className="font-bold text-sm uppercase tracking-wider">{t('emailPush')}</div>
                      <div className="text-xs text-gray-500 mt-1 font-mono uppercase">{t('emailDesc')}</div>
                    </div>
                    <ToggleSwitch checked={settings.notifications.email_enabled} onChange={(checked) => updateNotifications({ email_enabled: checked })} />
                  </div>
                  {settings.notifications.email_enabled && (
                    <div className="p-4 border border-black bg-[#E8E8E3]">
                      <TextField label={t('targetAddress')} value={settings.notifications.email_target} onChange={(value) => updateNotifications({ email_target: value })} placeholder="student@university.edu" type="email" />
                    </div>
                  )}
                </div>

                <SaveConfigButton saving={saving === 'notifications'} label={t('saveConfig')} onClick={saveNotificationSettings} />
              </div>
            )}
          </section>

          <section>
            <AccordionHeader expanded={expanded.logs} icon={<FileText size={14} />} label={t('runLogs')} onClick={() => toggleSection('logs')} />
            {expanded.logs && (
              <div className="settings-panel border border-black bg-[#F4F4F0] p-6 space-y-4">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                  <div className="grid grid-cols-2 sm:flex sm:flex-wrap gap-2" role="group" aria-label={t('runLogs')}>
                    {(['all', 'success', 'failed', 'warning'] as const).map((filter) => (
                      <button
                        key={filter}
                        onClick={() => setEventLogFilter(filter)}
                        className={`flex items-center justify-center gap-2 px-3 py-2 text-[10px] font-mono font-bold tracking-widest uppercase border border-black transition-colors ${
                          eventLogFilter === filter ? 'bg-black text-[#F4F4F0]' : 'bg-[#F4F4F0] text-black hover:bg-white'
                        }`}
                      >
                        <span>{eventLogFilterLabel(filter, t)}</span>
                        <span className={eventLogFilter === filter ? 'text-[#F4F4F0]' : 'text-gray-500'}>{eventLogCounts[filter]}</span>
                      </button>
                    ))}
                  </div>
                  <button
                    onClick={loadEventLogs}
                    disabled={isLoadingEvents}
                    className="flex items-center justify-center gap-2 px-4 py-2 text-xs font-mono font-bold tracking-widest uppercase border border-black bg-[#F4F4F0] text-black hover:bg-black hover:text-[#F4F4F0] disabled:bg-[#E8E8E3] disabled:text-gray-500 disabled:cursor-wait"
                  >
                    <RefreshCcw size={14} className={isLoadingEvents ? 'animate-spin' : ''} />
                    {t('refreshLogs')}
                  </button>
                </div>
                <EventLogList events={eventLogs} filter={eventLogFilter} />
              </div>
            )}
          </section>

          <section className="border border-black bg-[#F4F4F0] p-6 flex items-start gap-4">
            <ShieldCheck size={24} strokeWidth={1.5} />
            <div>
              <h2 className="font-bold text-sm uppercase tracking-wider mb-2">{t('readOnlyBoundary')}</h2>
              <p className="text-sm text-gray-700 font-medium leading-relaxed">{t('readOnlyNotice')}</p>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
