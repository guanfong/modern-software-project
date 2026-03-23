import axios from 'axios';

/**
 * Human-readable message from axios/FastAPI errors (detail string, 422 list, HTML, timeouts).
 */
export function getApiErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.code === 'ECONNABORTED') {
      return 'Request timed out. Transcription and AI processing can take 1–3+ minutes for longer audio — try a shorter file or wait and retry.';
    }
    const status = error.response?.status;
    const data = error.response?.data;

    if (data === undefined || data === null || data === '') {
      if (error.message) return error.message;
      return status ? `Request failed (HTTP ${status})` : 'Network error — no response from server';
    }

    if (typeof data === 'string') {
      const stripped = data.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
      if (stripped.length > 0) {
        return stripped.length > 280 ? `${stripped.slice(0, 280)}…` : stripped;
      }
      return status ? `HTTP ${status}` : 'Unknown server error';
    }

    const obj = data as Record<string, unknown>;
    const detail = obj.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item: unknown) => {
          if (typeof item === 'object' && item !== null && 'msg' in item) {
            return String((item as { msg: unknown }).msg);
          }
          return JSON.stringify(item);
        })
        .join('; ');
    }
    if (typeof obj.message === 'string') return obj.message;
    try {
      return JSON.stringify(obj);
    } catch {
      return error.message || 'Request failed';
    }
  }

  if (error instanceof Error) return error.message;
  return String(error);
}
