import type { AgentChatMessage, EventLog } from '../types';
import { api } from './client';

export type AgentStreamToolEvent = {
  name: string;
  ok: boolean;
  error?: string | null;
};

export type AgentStreamHandlers = {
  onDelta?: (content: string) => void;
  onTool?: (event: AgentStreamToolEvent) => void;
  onDone?: (message: AgentChatMessage) => void;
  onStatus?: (status: string) => void;
};

export function sendAgentMessage(
  message: string,
  history: AgentChatMessage[],
  courseId?: number | null,
  sessionId?: string | null,
  sessionTitle?: string | null
): Promise<AgentChatMessage> {
  return api<AgentChatMessage>('/api/agent/chat', {
    method: 'POST',
    body: JSON.stringify({
      message,
      history: history.map((item) => ({ role: item.role, content: item.content })),
      course_id: courseId ?? null,
      session_id: sessionId ?? null,
      session_title: sessionTitle ?? null
    })
  });
}

export function fetchAgentEventLogs(limit = 80): Promise<EventLog[]> {
  return api<EventLog[]>(`/api/events?limit=${limit}&category=ai`);
}

export async function streamAgentMessage(
  message: string,
  history: AgentChatMessage[],
  courseId: number | null | undefined,
  sessionId: string | null | undefined,
  sessionTitle: string | null | undefined,
  handlers: AgentStreamHandlers
): Promise<AgentChatMessage> {
  const response = await fetch('/api/agent/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      history: history.map((item) => ({ role: item.role, content: item.content })),
      course_id: courseId ?? null,
      session_id: sessionId ?? null,
      session_title: sessionTitle ?? null
    })
  });

  if (!response.ok) throw new Error((await response.text()) || response.statusText || `HTTP ${response.status}`);
  if (!response.body) throw new Error('Streaming response body is unavailable.');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let doneMessage: AgentChatMessage | null = null;

  function handleEvent(event: Record<string, unknown> | null) {
    if (!event) return;
    if (event.type === 'delta' && typeof event.content === 'string') {
      handlers.onDelta?.(event.content);
    } else if (event.type === 'tool' && typeof event.name === 'string') {
      handlers.onTool?.({ name: event.name, ok: Boolean(event.ok), error: typeof event.error === 'string' ? event.error : null });
    } else if (event.type === 'status' && typeof event.status === 'string') {
      handlers.onStatus?.(event.status);
    } else if (event.type === 'error') {
      throw new Error(typeof event.message === 'string' ? event.message : 'Agent stream failed.');
    } else if (event.type === 'done' && event.message) {
      doneMessage = event.message as AgentChatMessage;
      handlers.onDone?.(doneMessage);
    }
  }

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      handleEvent(parseStreamLine(line));
    }
  }

  if (buffer.trim()) {
    handleEvent(parseStreamLine(buffer));
  }
  if (!doneMessage) throw new Error('Agent stream ended without a final message.');
  return doneMessage;
}

function parseStreamLine(line: string): Record<string, unknown> | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  try {
    const event = JSON.parse(trimmed);
    return event && typeof event === 'object' ? event : null;
  } catch {
    return null;
  }
}
