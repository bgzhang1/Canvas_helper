import { FormEvent, KeyboardEvent, MouseEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Bot, ChevronDown, ChevronUp, MessageSquarePlus, RefreshCcw, Send, Square, Trash2, UserRound } from 'lucide-react';
import type { AgentChatMessage, Course, EventLog } from '../types';
import { useAppContext } from '../context/AppContext';
import { fetchAgentEventLogs, streamAgentMessage } from '../api/agent';
import { fetchAgentModels, saveAgentModel } from '../api/settings';
import { Badge, EmptyState } from '../components/ui';
import { MarkdownContent } from '../components/MarkdownContent';
import { fmtDate } from '../utils/format';
import { eventActionLabel, eventLogBadgeVariant, eventStatusLabel } from '../utils/labels';

type AgentChatSession = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  courseId: number | null;
  messages: AgentChatMessage[];
};

const SESSIONS_KEY = 'canvas.agent.sessions.v1';
const ACTIVE_SESSION_KEY = 'canvas.agent.activeSession.v1';

export function AgentChatView({
  selectedCourseId,
  setSelectedCourseId
}: {
  courses: Course[];
  selectedCourseId: number | null;
  setSelectedCourseId: (value: number | null) => void;
}) {
  const { setError, t } = useAppContext();
  const initial = useMemo(() => loadStoredSessions(), []);
  const [sessions, setSessions] = useState<AgentChatSession[]>(initial.sessions);
  const [activeSessionId, setActiveSessionId] = useState(initial.activeSessionId);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [queued, setQueued] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const [agentLogs, setAgentLogs] = useState<EventLog[]>([]);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState('');
  const [loadingModels, setLoadingModels] = useState(false);
  const [menu, setMenu] = useState<{ sessionId: string; x: number; y: number } | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);
  const [elapsed, setElapsed] = useState(0);

  const activeSession = sessions.find((session) => session.id === activeSessionId) ?? sessions[0];
  const messages = activeSession?.messages ?? [];

  useEffect(() => {
    if (activeSession && activeSession.id !== activeSessionId) setActiveSessionId(activeSession.id);
  }, [activeSession, activeSessionId]);

  useEffect(() => {
    const handle = window.setTimeout(() => persistSessions(sessions, activeSessionId), sending ? 600 : 0);
    return () => window.clearTimeout(handle);
  }, [sessions, activeSessionId, sending]);

  useEffect(() => {
    setSelectedCourseId(activeSession?.courseId ?? null);
  }, [activeSession?.id, activeSession?.courseId, setSelectedCourseId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && atBottomRef.current) el.scrollTo({ top: el.scrollHeight });
  }, [messages, sending]);

  useEffect(() => {
    atBottomRef.current = true;
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight });
  }, [activeSessionId]);

  useEffect(() => {
    if (!sending) {
      setElapsed(0);
      return;
    }
    const start = Date.now();
    const id = window.setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, [sending]);

  useEffect(() => {
    loadAgentLogs().catch(() => undefined);
    loadModels().catch(() => undefined);
  }, []);

  useEffect(() => {
    if (sending || queued.length === 0) return;
    const [next, ...rest] = queued;
    setQueued(rest);
    void sendMessage(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sending, queued]);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (event: globalThis.KeyboardEvent) => event.key === 'Escape' && setMenu(null);
    window.addEventListener('click', close);
    window.addEventListener('scroll', close, true);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('click', close);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('keydown', onKey);
    };
  }, [menu]);

  async function loadAgentLogs() {
    setLoadingLogs(true);
    try {
      setAgentLogs(await fetchAgentEventLogs());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingLogs(false);
    }
  }

  async function loadModels() {
    setLoadingModels(true);
    try {
      const result = await fetchAgentModels();
      setModels(result.models);
      setModel(result.model);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoadingModels(false);
    }
  }

  async function changeModel(next: string) {
    setModel(next);
    try {
      await saveAgentModel(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function startNewChat() {
    const next = createSession();
    setSessions((current) => [next, ...current]);
    setActiveSessionId(next.id);
    setSelectedCourseId(null);
    setDraft('');
    setError(null);
  }

  function selectSession(session: AgentChatSession) {
    setActiveSessionId(session.id);
    setSelectedCourseId(session.courseId);
    setDraft('');
    setError(null);
  }

  function openSessionMenu(event: MouseEvent, sessionId: string) {
    event.preventDefault();
    setMenu({ sessionId, x: event.clientX, y: event.clientY });
  }

  function deleteSession(sessionId: string) {
    setMenu(null);
    const remaining = sessions.filter((session) => session.id !== sessionId);
    if (remaining.length === 0) {
      const fresh = createSession();
      setSessions([fresh]);
      setActiveSessionId(fresh.id);
    } else {
      setSessions(remaining);
      if (sessionId === activeSessionId) setActiveSessionId(remaining[0].id);
    }
  }

  function interrupt() {
    abortRef.current?.abort();
    setQueued([]);
  }

  function handleScroll() {
    const el = scrollRef.current;
    if (el) atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  function submit(event?: FormEvent) {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || !activeSession) return;
    setDraft('');
    if (sending) {
      setQueued((current) => [...current, content]);
      return;
    }
    void sendMessage(content);
  }

  async function sendMessage(content: string) {
    const session = sessions.find((item) => item.id === activeSessionId) ?? activeSession;
    if (!session) return;
    const sessionId = session.id;
    const now = new Date().toISOString();
    const nextTitle = session.messages.length === 0 ? titleFromMessage(content) : session.title;
    const nextMessages: AgentChatMessage[] = [...session.messages, { role: 'user', content }];
    const controller = new AbortController();
    abortRef.current = controller;
    setSessions((current) =>
      current.map((item) =>
        item.id === sessionId
          ? { ...item, title: nextTitle, courseId: selectedCourseId, messages: [...nextMessages, { role: 'assistant', content: '', tools_used: [], status: 'streaming' }], updatedAt: now }
          : item
      )
    );
    setSending(true);
    setError(null);
    try {
      await streamAgentMessage(
        content,
        nextMessages,
        selectedCourseId,
        sessionId,
        nextTitle,
        {
          onDelta: (chunk) => appendAssistantContent(sessionId, chunk),
          onThinking: (chunk) => appendAssistantThinking(sessionId, chunk),
          onTool: (tool) => updateAssistantStep(sessionId, tool),
          onDone: (reply) => replaceAssistantMessage(sessionId, reply)
        },
        controller.signal
      );
      await loadAgentLogs();
    } catch (err) {
      if (controller.signal.aborted) {
        finalizeStreamingMessage(sessionId);
      } else {
        const reason = err instanceof Error ? err.message : String(err);
        setError(reason);
        replaceAssistantMessage(sessionId, { role: 'assistant', content: reason, status: 'error' });
      }
      await loadAgentLogs();
    } finally {
      abortRef.current = null;
      setSending(false);
    }
  }

  function finalizeStreamingMessage(sessionId: string) {
    setSessions((current) =>
      current.map((session) => {
        if (session.id !== sessionId) return session;
        const messages = [...session.messages];
        const last = messages[messages.length - 1];
        if (last?.role === 'assistant' && last.status === 'streaming') {
          const steps = last.steps?.map((step) => (step.status === 'running' ? { ...step, status: 'ok' as const } : step));
          messages[messages.length - 1] = { ...last, status: 'ok', steps };
        }
        return { ...session, messages, updatedAt: new Date().toISOString() };
      })
    );
  }

  function handleDraftKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && event.ctrlKey) {
      event.preventDefault();
      void submit();
    }
  }

  function appendAssistantContent(sessionId: string, chunk: string) {
    setSessions((current) =>
      current.map((session) => {
        if (session.id !== sessionId) return session;
        const messages = [...session.messages];
        const last = messages[messages.length - 1];
        if (last?.role === 'assistant') {
          messages[messages.length - 1] = { ...last, content: `${last.content}${chunk}` };
        }
        return { ...session, messages, updatedAt: new Date().toISOString() };
      })
    );
  }

  function appendAssistantThinking(sessionId: string, chunk: string) {
    setSessions((current) =>
      current.map((session) => {
        if (session.id !== sessionId) return session;
        const messages = [...session.messages];
        const last = messages[messages.length - 1];
        if (last?.role === 'assistant') {
          messages[messages.length - 1] = { ...last, thinking: `${last.thinking ?? ''}${chunk}` };
        }
        return { ...session, messages, updatedAt: new Date().toISOString() };
      })
    );
  }

  function updateAssistantStep(sessionId: string, tool: { name: string; phase: 'start' | 'end'; ok: boolean; arguments?: Record<string, unknown> | null }) {
    setSessions((current) =>
      current.map((session) => {
        if (session.id !== sessionId) return session;
        const messages = [...session.messages];
        const last = messages[messages.length - 1];
        if (last?.role !== 'assistant') return session;
        const steps = [...(last.steps ?? [])];
        if (tool.phase === 'start') {
          steps.push({ name: tool.name, status: 'running', args: tool.arguments ?? null });
        } else {
          const status = tool.ok ? 'ok' : 'error';
          let idx = steps.findIndex((step) => step.name === tool.name && step.status === 'running');
          if (idx < 0) idx = steps.map((step) => step.name).lastIndexOf(tool.name);
          if (idx >= 0) steps[idx] = { name: tool.name, status, args: tool.arguments ?? steps[idx].args ?? null };
          else steps.push({ name: tool.name, status, args: tool.arguments ?? null });
        }
        const tools = tool.ok ? Array.from(new Set([...(last.tools_used ?? []), tool.name])) : last.tools_used;
        messages[messages.length - 1] = { ...last, steps, tools_used: tools };
        return { ...session, messages, updatedAt: new Date().toISOString() };
      })
    );
  }

  function replaceAssistantMessage(sessionId: string, reply: AgentChatMessage) {
    setSessions((current) =>
      current.map((session) => {
        if (session.id !== sessionId) return session;
        const messages = [...session.messages];
        const last = messages[messages.length - 1];
        if (last?.role === 'assistant') {
          const steps = (reply.steps ?? last.steps)?.map((step) => (step.status === 'running' ? { ...step, status: 'ok' as const } : step));
          messages[messages.length - 1] = { ...reply, thinking: reply.thinking ?? last.thinking, steps };
        } else {
          messages.push(reply);
        }
        return { ...session, messages, updatedAt: new Date().toISOString() };
      })
    );
  }

  return (
    <div className="agent-root h-[calc(100vh-10rem)] min-h-[520px] grid grid-cols-[18rem_minmax(0,1fr)] gap-4">
      <aside className="agent-sidebar min-h-0 border border-black bg-[#F4F4F0] flex flex-col">
        <div className="border-b border-black p-3">
          <button
            type="button"
            onClick={startNewChat}
            disabled={sending}
            className="w-full flex items-center justify-center gap-2 border border-black bg-black px-3 py-2 text-xs font-mono font-bold uppercase tracking-widest text-[#F4F4F0] hover:bg-[#F4F4F0] hover:text-black disabled:bg-[#E8E8E3] disabled:text-gray-500"
          >
            <MessageSquarePlus size={14} />
            {t('newChat')}
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="px-3 pt-3 pb-2 text-[10px] font-mono uppercase tracking-widest text-gray-500">{t('chatHistory')}</div>
          {sessions.length === 0 ? (
            <div className="px-3">
              <EmptyState>{t('noConversations')}</EmptyState>
            </div>
          ) : (
            <div className="space-y-1 px-2 pb-3">
              {sessions.map((session) => (
                <button
                  key={session.id}
                  type="button"
                  onClick={() => selectSession(session)}
                  onContextMenu={(event) => openSessionMenu(event, session.id)}
                  title={t('deleteChat')}
                  className={`w-full border px-3 py-2 text-left transition-none ${
                    session.id === activeSession?.id ? 'border-black bg-[#E8E8E3]' : 'border-transparent hover:border-black'
                  }`}
                >
                  <div className="truncate text-xs font-mono font-bold uppercase tracking-wider">{session.title || t('conversationUntitled')}</div>
                  <div className="mt-1 flex items-center justify-between gap-2 text-[10px] font-mono uppercase tracking-widest text-gray-500">
                    <span>{session.messages.length}</span>
                    <span className="truncate">{fmtDate(session.updatedAt)}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </aside>

      <section className="agent-chat-panel min-w-0 min-h-0 flex flex-col gap-3">
        <div className="flex items-center gap-2 border border-black bg-[#F4F4F0] px-3 py-2">
          <label className="shrink-0 text-[10px] font-mono font-bold uppercase tracking-widest text-gray-500">{t('modelSelect')}</label>
          <select
            value={model}
            onChange={(event) => void changeModel(event.target.value)}
            className="min-w-0 flex-1 border border-black bg-white px-2 py-1 text-xs font-mono outline-none focus:bg-[#E8E8E3]"
          >
            {(model && !models.includes(model) ? [model, ...models] : models).length === 0 && <option value="">{t('notConfigured')}</option>}
            {(model && !models.includes(model) ? [model, ...models] : models).map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => void loadModels()}
            disabled={loadingModels}
            className="h-7 w-7 shrink-0 border border-black flex items-center justify-center bg-white hover:bg-black hover:text-[#F4F4F0] disabled:text-gray-500"
            aria-label={t('modelSelect')}
          >
            <RefreshCcw size={13} className={loadingModels ? 'animate-spin' : ''} />
          </button>
        </div>
        <div className="relative flex-1 min-h-0 border border-black bg-[#F4F4F0] overflow-hidden">
          <div ref={scrollRef} onScroll={handleScroll} className="h-full min-h-0 overflow-y-auto">
            {messages.length === 0 ? (
              <div className="h-full min-h-[480px] flex items-center justify-center text-[10px] font-mono tracking-widest uppercase text-gray-500">
                {t('agentReady')}
              </div>
            ) : (
              <div className={`divide-y divide-black ${logsOpen ? 'pb-72' : 'pb-12'}`}>
                {messages.map((message, index) => (
                  <div key={`${message.role}-${index}`} className="grid grid-cols-[44px_1fr] gap-4 p-5">
                    <div className="h-8 w-8 border border-black flex items-center justify-center bg-white">
                      {message.role === 'user' ? <UserRound size={15} /> : <Bot size={15} />}
                    </div>
                    <div className="min-w-0">
                      <div className="mb-2 flex items-center gap-2 text-[10px] font-mono font-bold uppercase tracking-widest">
                        <span>{message.role === 'user' ? t('you') : t('agent')}</span>
                        {message.role === 'assistant' && message.status === 'streaming' && (
                          <span className="animate-pulse text-gray-500">
                            {t('agentWorking')}
                            {elapsed > 0 ? ` / ${elapsed}s` : ''}
                          </span>
                        )}
                        {message.role === 'assistant' && message.status === 'error' && (
                          <span className="text-red-700">{t('agentFailed')}</span>
                        )}
                      </div>
                      {(message.thinking || (message.steps && message.steps.length > 0)) && (
                        <details className="mb-2 border border-dashed border-gray-400 bg-white/50 p-2" open={message.status === 'streaming'}>
                          <summary className="cursor-pointer text-[10px] font-mono uppercase tracking-widest text-gray-500">{t('agentThinking')}</summary>
                          {message.thinking && (
                            <div className="mt-2 whitespace-pre-wrap text-[11px] font-mono text-gray-600">{message.thinking}</div>
                          )}
                          {message.steps && message.steps.length > 0 && (
                            <div className="mt-2 space-y-1">
                              {message.steps.map((step, stepIndex) => {
                                const icon = step.status === 'running' ? '...' : step.status === 'ok' ? 'OK' : 'ERR';
                                const detail = step.args && Object.keys(step.args).length > 0 ? truncate(JSON.stringify(step.args, null, 2), 1500) : null;
                                const label = (
                                  <>
                                    <span>{icon}</span>
                                    <span className={step.status === 'running' ? 'animate-pulse' : ''}>{step.name}</span>
                                  </>
                                );
                                return detail ? (
                                  <details key={`${step.name}-${stepIndex}`} className="text-[10px] font-mono uppercase tracking-widest text-gray-600">
                                    <summary className="flex cursor-pointer items-center gap-2">{label}</summary>
                                    <pre className="ml-5 mt-1 whitespace-pre-wrap break-all border border-dashed border-gray-400 bg-white/60 p-2 text-[10px] normal-case tracking-normal text-gray-700">{detail}</pre>
                                  </details>
                                ) : (
                                  <div key={`${step.name}-${stepIndex}`} className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-gray-600">
                                    {label}
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </details>
                      )}
                      <MarkdownContent content={message.content} />
                    </div>
                  </div>
                ))}
                {sending && messages[messages.length - 1]?.role !== 'assistant' && (
                  <div className="grid grid-cols-[44px_1fr] gap-4 p-5">
                    <div className="h-8 w-8 border border-black flex items-center justify-center bg-white">
                      <Bot size={15} className="animate-pulse" />
                    </div>
                    <div className="text-[10px] font-mono font-bold uppercase tracking-widest pt-2">{t('agentThinking')}</div>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className={`absolute left-0 right-0 bottom-0 border-t border-black bg-white transition-transform duration-200 ${logsOpen ? 'translate-y-0' : 'translate-y-[calc(100%-40px)]'}`}>
            <button
              type="button"
              onClick={() => setLogsOpen((open) => !open)}
              className="h-10 w-full flex items-center justify-between gap-3 px-4 text-[10px] font-mono font-bold uppercase tracking-widest hover:bg-black hover:text-[#F4F4F0]"
            >
              <span className="flex items-center gap-2">
                {logsOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
                {t('agentLogs')}
              </span>
              <span className="text-gray-500">{agentLogs.length}</span>
            </button>
            <div className="h-64 border-t border-black bg-white">
              <div className="flex items-center justify-between border-b border-black px-3 py-2">
                <div className="text-[10px] font-mono uppercase tracking-widest text-gray-500">{t('runLogs')}</div>
                <button
                  type="button"
                  onClick={() => void loadAgentLogs()}
                  disabled={loadingLogs}
                  className="h-7 w-7 border border-black flex items-center justify-center bg-white hover:bg-black hover:text-[#F4F4F0] disabled:text-gray-500"
                  aria-label={t('refreshLogs')}
                >
                  <RefreshCcw size={13} className={loadingLogs ? 'animate-spin' : ''} />
                </button>
              </div>
              <div className="h-[calc(16rem-45px)] overflow-y-auto">
                {agentLogs.length === 0 ? (
                  <div className="p-3 text-[10px] font-mono uppercase tracking-widest text-gray-500">{t('noAgentLogs')}</div>
                ) : (
                  agentLogs.map((event, index) => (
                    <div key={event.id} className={`p-3 ${index !== agentLogs.length - 1 ? 'border-b border-black' : ''}`}>
                      <div className="mb-1 flex flex-wrap items-center gap-1">
                        <Badge variant={eventLogBadgeVariant(event.status)}>{eventStatusLabel(event.status, t)}</Badge>
                        <span className="text-[10px] font-mono text-gray-500">{fmtDate(event.created_at)}</span>
                      </div>
                      <div className="truncate text-[11px] font-mono font-bold">
                        {eventActionLabel(event.action, t)}
                        {event.item_name ? ` / ${event.item_name}` : ''}
                      </div>
                      {event.message && (
                        <div className="mt-1 line-clamp-3 break-all text-[10px] font-mono text-red-700">{event.message}</div>
                      )}
                      {eventDetailLines(event).map((line, lineIndex) => (
                        <div key={lineIndex} className="mt-1 break-all text-[10px] font-mono text-gray-600">{line}</div>
                      ))}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>

        <form onSubmit={submit} className="agent-compose flex flex-col gap-1">
          {queued.length > 0 && (
            <div className="text-[10px] font-mono uppercase tracking-widest text-gray-500">
              {t('queuedCount')}: {queued.length}
            </div>
          )}
          <div className="flex gap-2">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleDraftKeyDown}
              rows={2}
              placeholder={t('agentInputPlaceholder')}
              className="min-h-[56px] flex-1 resize-none border border-black bg-white px-4 py-2 text-sm font-mono outline-none focus:bg-[#E8E8E3]"
            />
            {sending && (
              <button
                type="button"
                onClick={interrupt}
                className="w-16 shrink-0 border border-black bg-white text-black flex items-center justify-center hover:bg-black hover:text-[#F4F4F0]"
                aria-label={t('interruptAgent')}
                title={t('interruptAgent')}
              >
                <Square size={16} />
              </button>
            )}
            <button
              type="submit"
              disabled={!draft.trim()}
              className="w-16 shrink-0 border border-black bg-black text-[#F4F4F0] flex items-center justify-center hover:bg-[#F4F4F0] hover:text-black disabled:bg-[#E8E8E3] disabled:text-gray-500"
              aria-label={t('sendMessage')}
            >
              <Send size={18} />
            </button>
          </div>
        </form>
      </section>

      {menu && (
        <div
          className="fixed z-50 border border-black bg-white shadow-md"
          style={{ top: menu.y, left: menu.x }}
          onClick={(event) => event.stopPropagation()}
        >
          <button
            type="button"
            onClick={() => deleteSession(menu.sessionId)}
            className="flex items-center gap-2 px-3 py-2 text-[11px] font-mono uppercase tracking-widest text-red-700 hover:bg-black hover:text-[#F4F4F0]"
          >
            <Trash2 size={13} />
            {t('deleteChat')}
          </button>
        </div>
      )}
    </div>
  );
}

function loadStoredSessions(): { sessions: AgentChatSession[]; activeSessionId: string } {
  const fallback = createSession();
  try {
    const raw = window.localStorage.getItem(SESSIONS_KEY);
    const parsed = raw ? (JSON.parse(raw) as AgentChatSession[]) : [];
    const sessions = parsed.filter(isStoredSession).slice(0, 30);
    if (!sessions.length) return { sessions: [fallback], activeSessionId: fallback.id };
    const storedActive = window.localStorage.getItem(ACTIVE_SESSION_KEY);
    return { sessions, activeSessionId: sessions.some((session) => session.id === storedActive) ? storedActive! : sessions[0].id };
  } catch {
    return { sessions: [fallback], activeSessionId: fallback.id };
  }
}

function persistSessions(sessions: AgentChatSession[], activeSessionId: string) {
  try {
    window.localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions.slice(0, 30)));
    window.localStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId);
  } catch {
    // Storage failures should not break the chat surface.
  }
}

function createSession(): AgentChatSession {
  const now = new Date().toISOString();
  return {
    id: createId(),
    title: 'New chat',
    createdAt: now,
    updatedAt: now,
    courseId: null,
    messages: []
  };
}

function createId() {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function titleFromMessage(message: string) {
  return message.replace(/\s+/g, ' ').slice(0, 56) || 'New chat';
}

function truncate(text: string, limit: number) {
  return text.length > limit ? `${text.slice(0, limit)}\n[truncated]` : text;
}

function eventDetailLines(event: EventLog): string[] {
  const meta = event.metadata ?? {};
  const lines: string[] = [];
  if (meta.arguments && typeof meta.arguments === 'object') lines.push(`args: ${JSON.stringify(meta.arguments)}`);
  if (meta.status_code !== undefined) lines.push(`code: ${String(meta.status_code)}`);
  else if (typeof meta.error_type === 'string') lines.push(`type: ${meta.error_type}`);
  if (typeof meta.response_body === 'string' && meta.response_body) lines.push(`body: ${meta.response_body}`);
  if (Array.isArray(meta.tools_used) && meta.tools_used.length > 0) lines.push(`tools: ${meta.tools_used.join(', ')}`);
  return lines;
}

function isStoredSession(value: AgentChatSession) {
  return Boolean(
    value &&
      typeof value.id === 'string' &&
      typeof value.title === 'string' &&
      Array.isArray(value.messages) &&
      value.messages.every((message) => (message.role === 'user' || message.role === 'assistant') && typeof message.content === 'string')
  );
}
