export type Course = {
  id: number;
  name: string;
  course_code: string | null;
  term_name: string | null;
  term_id: number | null;
  term_start_at: string | null;
  term_end_at: string | null;
  workflow_state: string | null;
  synced_at: string;
  announcement_count: number;
  assignment_count: number;
  file_count: number;
  downloaded_count: number;
  upcoming_count: number;
};

export type Announcement = {
  id: number;
  title: string;
  message: string | null;
  posted_at: string | null;
  author_name: string | null;
  html_url: string | null;
};

export type Assignment = {
  id: number;
  name: string;
  due_at: string | null;
  unlock_at: string | null;
  lock_at: string | null;
  workflow_state: string | null;
  points_possible: number | null;
  score: number | null;
  grade: string | null;
  submitted_at: string | null;
  submission_workflow_state: string | null;
  description: string | null;
  html_url: string | null;
  submission_types: string[];
  allowed_extensions: string[];
  assignment_group_name: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type MaterialFile = {
  id: number;
  display_name: string;
  filename: string;
  content_type: string | null;
  size: number | null;
  updated_at: string | null;
  local_path: string | null;
  folder_path: string;
  backup_status: string;
  backup_error: string | null;
  extraction_status: string;
  extraction_error: string | null;
  outline: Array<{ title: string }>;
  extracted_at: string | null;
};

export type Person = {
  id: number;
  name: string;
  sortable_name: string | null;
  email: string | null;
  role: string | null;
  last_activity_at: string | null;
};

export type TimelineItem = {
  item_id?: number | string | null;
  title: string;
  date: string;
  source: string;
  confidence?: string;
  confidence_reason?: string;
};

export type Analysis = {
  summary?: string;
  timeline?: TimelineItem[];
  course_outline?: Array<{ title: string; evidence?: string }>;
  risks?: string[];
  confidence_notes?: string[];
  model?: string;
  generated_at?: string;
};

export type TimelineResponse = {
  structured: TimelineItem[];
  analysis: Analysis | null;
  data_sources: Record<string, { count?: number; available?: boolean }>;
};

export type CourseHome = {
  page_url: string;
  page_id: number | null;
  title: string;
  body: string | null;
  updated_at: string | null;
  published: number | null;
};

export type CourseDetail = {
  announcements: Announcement[];
  assignments: Assignment[];
  files: MaterialFile[];
  people: Person[];
  timeline: TimelineResponse;
  home: CourseHome | null;
};

export type SyncStatus = {
  running: boolean;
  cancel_requested?: boolean;
  run: null | {
    id: number;
    started_at: string;
    finished_at: string | null;
    status: string;
    message: string | null;
    counts_json: string;
  };
};

export type SyncProgress = {
  percent: number;
  stage: string;
  current?: number;
  total?: number;
  course?: string | null;
  file?: string | null;
  phase?: string;
  status?: string;
};

export type AnalysisStatus = {
  running: boolean;
  status: string;
  percent: number;
  stage: string;
  course_id?: number | null;
  course?: string | null;
  file?: string | null;
  current?: number | null;
  total?: number | null;
  message?: string | null;
};

export type AppSettings = {
  canvas_base_url: string;
  token_configured: boolean;
  sync: {
    enabled: boolean;
    interval_minutes: number;
  };
  ocr: {
    enabled: boolean;
    languages: string;
    max_pages: number;
  };
  ai: {
    base_url: string;
    configured: boolean;
    api_key_configured: boolean;
    model: string;
    reasoning_effort: string;
    skills: string;
  };
  notifications: {
    telegram_enabled: boolean;
    telegram_configured: boolean;
    telegram_chat_id: string;
    email_enabled: boolean;
    email_target: string;
  };
};

export type CanvasTestResult = {
  ok: boolean;
  canvas_base_url: string;
  username: string | null;
  message: string;
};

export type AIModelTestResult = {
  ok: boolean;
  message: string;
  model_count: number;
};

export type AIModelList = {
  ok: boolean;
  models: string[];
  message: string;
  model: string;
};

export type EventLog = {
  id: number;
  created_at: string;
  category: string;
  action: string;
  status: string;
  title: string;
  course_id: number | null;
  course_name: string | null;
  item_id: string | null;
  item_name: string | null;
  message: string | null;
  metadata: Record<string, unknown>;
};

export type AgentChatMessage = {
  role: 'user' | 'assistant';
  content: string;
  tools_used?: string[];
  status?: string;
  thinking?: string;
  steps?: { name: string; status: 'running' | 'ok' | 'error'; args?: Record<string, unknown> | null }[];
};

export type ActiveTab = 'timeline' | 'files' | 'announcements' | 'assignments' | 'people';
export type View = 'dashboard' | 'agent' | 'course' | 'settings';
export type EventLogFilter = 'all' | 'success' | 'failed' | 'warning';
export type EventLogLevel = Exclude<EventLogFilter, 'all'> | 'running' | 'other';
