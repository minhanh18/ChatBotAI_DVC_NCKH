// ── Types ─────────────────────────────────────────────────────────────────────

export interface Citation {
  document_name: string;
  content: string;
  score: number;
  segment_id: string;
  url?: string;
  source_type?: 'document' | 'web' | string;
  domain?: string;
  page_date?: string;
  fetched_at?: string;
  reliability_score?: number;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  answer_mode?: 'rag' | 'ai';
  citations: Citation[];
  created_at: string;
  feedback?: 'like' | 'dislike' | null;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface Dataset {
  id: string;
  name: string;
  description: string;
  document_count: number;
  ready_count: number;
  created_at: string;
}

export interface Document {
  id: string;
  name: string;
  file_type: string;
  file_size: number;
  status: 'pending' | 'indexing' | 'ready' | 'error';
  chunk_count: number;
  error_message?: string;
  created_at: string;
}

export interface SSEEvent {
  type: 'token' | 'citations' | 'done' | 'error' | 'mode' | 'conversation_id';
  data: any;
}

export interface AdminAuth {
  user: string;
  pass: string;
}

export interface FeedbackPayload {
  rating: 'like' | 'dislike';
  issue_type?: string;
  description?: string;
  toggle?: boolean;
}

const BASE = '/api';

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, options);
  const contentType = res.headers.get('content-type') || '';
  if (!res.ok) {
    const body = await res.text();
    const err: any = new Error(body || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  if (contentType.includes('application/json')) {
    return res.json();
  }
  return (await res.text()) as T;
}

function normalizeConversationTitle(title: string) {
  return title === 'New conversation' ? 'Hội thoại mới' : title;
}

function authHeaders(auth?: AdminAuth) {
  return auth ? { Authorization: `Basic ${btoa(`${auth.user}:${auth.pass}`)}` } : {};
}

export interface ChatStreamCallbacks {
  onConversationId?: (id: string) => void;
  onMode?: (mode: 'rag' | 'ai') => void;
  onToken?: (token: string) => void;
  onCitations?: (citations: Citation[]) => void;
  onDone?: (meta: { tokens: number; latency_ms: number }) => void;
  onError?: (err: string) => void;
}

async function consumeSSE(
  res: Response,
  callbacks: ChatStreamCallbacks,
) {
  if (!res.ok || !res.body) {
    throw new Error(`HTTP ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      try {
        const event: SSEEvent = JSON.parse(line.slice(6));
        switch (event.type) {
          case 'conversation_id': callbacks.onConversationId?.(event.data); break;
          case 'mode': callbacks.onMode?.(event.data); break;
          case 'token': callbacks.onToken?.(event.data); break;
          case 'citations': callbacks.onCitations?.(event.data); break;
          case 'done': callbacks.onDone?.(event.data); break;
          case 'error': callbacks.onError?.(event.data); break;
        }
      } catch {
        // ignore malformed lines
      }
    }
  }
}

export async function streamChat(
  params: {
    query: string;
    conversation_id?: string;
    dataset_id?: string;
    mode?: 'rag' | 'ai';
    session_key?: string;
  },
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
) {
  const res = await fetch(`${BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
    signal,
  });

  return consumeSSE(res, callbacks);
}

export async function streamChatWithImage(
  params: {
    query?: string;
    conversation_id?: string;
    session_key?: string;
    image: File;
  },
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
) {
  const fd = new FormData();
  fd.append('query', params.query || '');
  if (params.conversation_id) fd.append('conversation_id', params.conversation_id);
  if (params.session_key) fd.append('session_key', params.session_key);
  fd.append('image', params.image);

  const res = await fetch(`${BASE}/chat/stream-image`, {
    method: 'POST',
    body: fd,
    signal,
  });

  return consumeSSE(res, callbacks);
}

export const getConversations = async (sessionKey?: string) => {
  const data = await apiFetch<Conversation[]>(`/chat/conversations${sessionKey ? `?session_key=${sessionKey}` : ''}`);
  return data.map((item) => ({ ...item, title: normalizeConversationTitle(item.title) }));
};

export const getMessages = (conversationId: string) =>
  apiFetch<Message[]>(`/chat/conversations/${conversationId}/messages`);

export const deleteConversation = (id: string) =>
  apiFetch(`/chat/conversations/${id}`, { method: 'DELETE' });

export const getDatasets = (auth: AdminAuth) =>
  apiFetch<Dataset[]>('/documents/datasets', { headers: authHeaders(auth) });

export const createDataset = (name: string, description: string, auth: AdminAuth) => {
  const fd = new FormData();
  fd.append('name', name);
  fd.append('description', description);
  return apiFetch('/documents/datasets', { method: 'POST', body: fd, headers: authHeaders(auth) });
};

export const deleteDataset = (id: string, auth: AdminAuth) =>
  apiFetch(`/documents/datasets/${id}`, { method: 'DELETE', headers: authHeaders(auth) });

export const getDocuments = (datasetId: string, auth: AdminAuth) =>
  apiFetch<Document[]>(`/documents/datasets/${datasetId}/documents`, { headers: authHeaders(auth) });

export const uploadDocument = (datasetId: string, file: File, auth: AdminAuth) => {
  const fd = new FormData();
  fd.append('file', file);
  return apiFetch(`/documents/datasets/${datasetId}/upload`, { method: 'POST', body: fd, headers: authHeaders(auth) });
};

export const renameDocument = (id: string, name: string, auth: AdminAuth) => {
  const fd = new FormData();
  fd.append('name', name);
  return apiFetch(`/documents/${id}`, { method: 'PATCH', body: fd, headers: authHeaders(auth) });
};

export const deleteDocument = (id: string, auth: AdminAuth) =>
  apiFetch(`/documents/${id}`, { method: 'DELETE', headers: authHeaders(auth) });

export const reindexDocument = (id: string, auth: AdminAuth) =>
  apiFetch(`/documents/${id}/reindex`, { method: 'POST', headers: authHeaders(auth) });

export const adminDashboard = (user: string, pass: string) =>
  apiFetch('/admin/dashboard', { headers: authHeaders({ user, pass }) });

export const adminLogs = (user: string, pass: string, limit = 50) =>
  apiFetch(`/admin/logs?limit=${limit}`, { headers: authHeaders({ user, pass }) });

export const adminDailyStats = (user: string, pass: string, days = 14) =>
  apiFetch(`/admin/stats/daily?days=${days}`, { headers: authHeaders({ user, pass }) });

export const adminFeedbackLogs = (user: string, pass: string, limit = 50) =>
  apiFetch(`/admin/feedback-logs?limit=${limit}`, { headers: authHeaders({ user, pass }) });

export const adminConversations = (user: string, pass: string, limit = 50) =>
  apiFetch(`/admin/conversations?limit=${limit}`, { headers: authHeaders({ user, pass }) });

export const submitMessageFeedback = (messageId: string, payload: FeedbackPayload) =>
  apiFetch<{ message: string; feedback?: 'like' | 'dislike' | null }>(`/chat/messages/${messageId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });


export const resetAdminMonitoring = (user: string, pass: string) =>
  apiFetch('/admin/reset-monitoring', { method: 'POST', headers: authHeaders({ user, pass }) });
