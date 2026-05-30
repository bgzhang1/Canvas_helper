import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Bot, ChevronDown, ChevronUp, MessageSquarePlus, RefreshCcw, Send, UserRound } from 'lucide-react';
import type { AgentChatMessage, Course, EventLog } from '../types';
import { useAppContext } from '../context/AppContext';
import { fetchAgentEventLogs, streamAgentMessage } from '../api/agent';
import { Badge, EmptyState } from '../components/ui';
import { MarkdownContent } from '../components/MarkdownContent';
import { fmtDate } from '../utils/format';
import { courseCode } from '../utils/course';
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
  courses,
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
  const [agentLogs, setAgentLogs] = useState<EventLog[]>([]);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const activeSession = sessions.find((session) => session.id === activeSessionId) ?? sessions[0];
  const messages = activeSession?.messages ?? [];

  useEffect(() => {
    if (activeSession && activeSession.id !== activeSessionId) setActiveSessionId(activeSession.id);
  }, [activeSession, activeSessionId]);

  useEffect(() => {
    persistSessions(sessions, activeSessionId);
  }, [sessions, activeSessionId]);

  useEffect(() => {
    setSelectedCourseId(activeSession?.courseId ?? null);
  }, [activeSession?.id]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, sending, activeSessionId]);

  useEffect(() => {
    loadAgentLogs().catch(() => undefined);
  }, []);

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

  function updateActiveSession(updater: (session: AgentChatSession) => AgentChatSession) {
    setSessions((current) => current.map((session) => (session.id === activeSessionId ? updater(session) : session)));
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

  function handleCourseChange(value: string) {
    const courseId = value ? Number(value) : null;
    setSelectedCourseId(courseId);
    updateActiveSession((session) => ({ ...session, courseId, updatedAt: new Date().toISOString() }));
  }

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || sending || !activeSession) return;
    const now = new Date().toISOString();
    const nextTitle = activeSession.messages.length === 0 ? titleFromMessage(content) : activeSession.title;
    const nextMessages: AgentChatMessage[] = [...activeSession.messages, { role: 'user', content }];
    setSessions((current) =>
      current.map((session) =>
        session.id === activeSession.id
          ? { ...session, title: nextTitle, courseId: selectedCourseId, messages: nextMessages, updatedAt: now }
          : session
      )
    );
    setDraft('');
    setSending(true);
    setError(null);
    try {
      const assistantPlaceholder: AgentChatMessage = { role: 'assistant', content: '', tools_used: [], status: 'streaming' };
      setSessions((current) =>
        current.map((session) =>
          session.id === activeSession.id
            ? { ...session, messages: [...nextMessages, assistantPlaceholder], updatedAt: new Date().toISOString() }
            : session
        )
      );
      await streamAgentMessage(content, nextMessages, selectedCourseId, activeSession.id, nextTitle, {
        onDelta: (chunk) => appendAssistantContent(activeSession.id, chunk),
        onTool: (tool) => addAssistantTool(activeSession.id, tool.name),
        onDone: (reply) => replaceAssistantMessage(activeSession.id, reply)
      });
      await loadAgentLogs();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSessions((current) =>
        current.map((session) => (session.id === activeSession.id ? { ...session, messages: nextMessages, updatedAt: now } : session))
      );
      await loadAgentLogs();
    } finally {
      setSending(false);
    }
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

  function addAssistantTool(sessionId: string, toolName: string) {
    setSessions((current) =>
      current.map((session) => {
        if (session.id !== sessionId) return session;
        const messages = [...session.messages];
        const last = messages[messages.length - 1];
        if (last?.role === 'assistant') {
          const tools = new Set([...(last.tools_used ?? []), toolName]);
          messages[messages.length - 1] = { ...last, tools_used: Array.from(tools) };
        }
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
          messages[messages.length - 1] = reply;
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
        <div className="agent-course-picker flex items-center justify-end gap-3">
          <select
            value={selectedCourseId ?? ''}
            onChange={(event) => handleCourseChange(event.target.value)}
            className="border border-black bg-[#F4F4F0] px-3 py-2 text-xs font-mono uppercase outline-none focus:bg-white"
          >
            <option value="">{t('allCourses')}</option>
            {courses.map((course) => (
              <option key={course.id} value={course.id}>
                {courseCode(course)}
              </option>
            ))}
          </select>
        </div>

        <div className="relative flex-1 min-h-0 border border-black bg-[#F4F4F0] overflow-hidden">
          <div ref={scrollRef} className="h-full min-h-0 overflow-y-auto">
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
                      <div className="mb-2 text-[10px] font-mono font-bold uppercase tracking-widest">{message.role === 'user' ? t('you') : t('agent')}</div>
                      <MarkdownContent content={message.content || (message.status === 'streaming' ? t('agentThinking') : '')} />
                      {message.tools_used && message.tools_used.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {message.tools_used.map((tool) => (
                            <span key={tool} className="border border-black px-2 py-0.5 text-[10px] font-mono uppercase">
                              {tool}
                            </span>
                          ))}
                        </div>
                      )}
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
                      <div className="truncate text-[11px] font-mono font-bold">{eventActionLabel(event.action, t)}</div>
                      {(event.item_name || event.message) && (
                        <div className="mt-1 line-clamp-2 text-[10px] font-mono text-gray-600">{event.item_name || event.message}</div>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>

        <form onSubmit={submit} className="agent-compose flex gap-2">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleDraftKeyDown}
            rows={2}
            placeholder={t('agentInputPlaceholder')}
            className="min-h-[56px] flex-1 resize-none border border-black bg-white px-4 py-2 text-sm font-mono outline-none focus:bg-[#E8E8E3]"
          />
          <button
            type="submit"
            disabled={!draft.trim() || sending}
            className="w-16 shrink-0 border border-black bg-black text-[#F4F4F0] flex items-center justify-center hover:bg-[#F4F4F0] hover:text-black disabled:bg-[#E8E8E3] disabled:text-gray-500 disabled:cursor-wait"
            aria-label={t('sendMessage')}
          >
            <Send size={18} />
          </button>
        </form>
      </section>
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

function isStoredSession(value: AgentChatSession) {
  return Boolean(
    value &&
      typeof value.id === 'string' &&
      typeof value.title === 'string' &&
      Array.isArray(value.messages) &&
      value.messages.every((message) => (message.role === 'user' || message.role === 'assistant') && typeof message.content === 'string')
  );
}
