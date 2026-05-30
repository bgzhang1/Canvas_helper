import type { AppSettings, CanvasTestResult, EventLog } from '../types';
import { api } from './client';

export type CanvasSettingsResponse = Pick<AppSettings, 'canvas_base_url' | 'token_configured'>;

export function fetchSettings(): Promise<AppSettings> {
  return api<AppSettings>('/api/settings');
}

export function saveCanvasSettings(apiToken: string): Promise<CanvasSettingsResponse> {
  return api<CanvasSettingsResponse>('/api/settings/canvas', {
    method: 'PUT',
    body: JSON.stringify({ api_token: apiToken })
  });
}

export function testCanvasSettings(apiToken: string): Promise<CanvasTestResult> {
  return api<CanvasTestResult>('/api/settings/canvas/test', {
    method: 'POST',
    body: JSON.stringify({ api_token: apiToken })
  });
}

export function saveSyncSettings(sync: AppSettings['sync']): Promise<AppSettings['sync']> {
  return api<AppSettings['sync']>('/api/settings/sync', {
    method: 'PUT',
    body: JSON.stringify(sync)
  });
}

export function saveAISettings(ai: AppSettings['ai'], apiKey: string): Promise<AppSettings['ai']> {
  return api<AppSettings['ai']>('/api/settings/ai', {
    method: 'PUT',
    body: JSON.stringify({
      base_url: ai.base_url,
      api_key: apiKey,
      model: ai.model,
      reasoning_effort: ai.reasoning_effort,
      skills: ai.skills
    })
  });
}

export function saveNotificationSettings(notifications: AppSettings['notifications'], telegramBotToken: string): Promise<AppSettings['notifications']> {
  return api<AppSettings['notifications']>('/api/settings/notifications', {
    method: 'PUT',
    body: JSON.stringify({
      telegram_enabled: notifications.telegram_enabled,
      telegram_bot_token: telegramBotToken,
      telegram_chat_id: notifications.telegram_chat_id,
      email_enabled: notifications.email_enabled,
      email_target: notifications.email_target
    })
  });
}

export function fetchEventLogs(limit = 200): Promise<EventLog[]> {
  return api<EventLog[]>(`/api/events?limit=${limit}`);
}
