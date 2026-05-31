import type { AgentChatMessage, EventLog } from '../types';
import { api } from './client';

export type AgentStreamToolEvent = {
  name: string;
  phase: 'start' | 'end';
  ok: boolean;
  error?: string | null;
  arguments?: Record<string, unknown> | null;
};

export type AgentStreamHandlers = {
  onDelta?: (content: string) => void;
  onThinking?: (content: string) => void;
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
  handlers: AgentStreamHandlers,
  signal?: AbortSignal
): Promise<AgentChatMessage> {
  const response = await fetch('/api/agent/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal,
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
  let streamedContent = '';
  const streamedTools = new Set<string>();

  function handleEvent(event: Record<string, unknown> | null) {
    if (!event) return;
    if (event.type === 'delta' && typeof event.content === 'string') {
      streamedContent += event.content;
      handlers.onDelta?.(event.content);
    } else if (event.type === 'thinking' && typeof event.content === 'string') {
      handlers.onThinking?.(event.content);
    } else if (event.type === 'tool' && typeof event.name === 'string') {
      if (event.phase !== 'start' && Boolean(event.ok)) streamedTools.add(event.name);
      handlers.onTool?.({
        name: event.name,
        phase: event.phase === 'start' ? 'start' : 'end',
        ok: Boolean(event.ok),
        error: typeof event.error === 'string' ? event.error : null,
        arguments: event.arguments && typeof event.arguments === 'object' ? (event.arguments as Record<string, unknown>) : null
      });
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
  if (!doneMessage) {
    // 兜底：流意外结束（连接中断或服务端未发送 done）时，保留已接收的增量内容，而不是丢弃并报错。
    const fallback: AgentChatMessage = {
      role: 'assistant',
      content: streamedContent.trim() || '(stream ended before completion)',
      tools_used: [...streamedTools],
      status: streamedContent.trim() ? 'ok' : 'error'
    };
    handlers.onDone?.(fallback);
    return fallback;
  }
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
