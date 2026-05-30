export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options?.headers ?? {}) },
    ...options
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const contentType = response.headers.get('content-type') ?? '';
    if (contentType.includes('application/json')) {
      const payload = await response.json();
      if (typeof payload?.detail === 'string') return payload.detail;
      return JSON.stringify(payload);
    }
    const text = await response.text();
    return text || response.statusText || `HTTP ${response.status}`;
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}

function downloadFromBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
}

export async function downloadSelection(courseId: number, fileIds: number[]) {
  const response = await fetch(`/api/courses/${courseId}/files/download`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_ids: fileIds })
  });
  if (!response.ok) throw new Error(await readErrorMessage(response));
  const blob = await response.blob();
  downloadFromBlob(blob, `course_${courseId}_files.zip`);
}
