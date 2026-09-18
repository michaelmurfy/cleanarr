export type Watcher = {
  user: string;
  plays: number;
  last_watched_at: number | null;
};

export type MediaItem = {
  id: number;
  media_type: "movie" | "tv";
  tmdb_id: number;
  tvdb_id: number;
  title: string;
  year: number | null;
  poster_url: string;
  size_bytes: number;
  radarr_id: number | null;
  sonarr_id: number | null;
  requested_by: string;
  requested_at: string;
  last_watched_at: number | null;
  play_count: number;
  watcher_count: number;
  watchers: Watcher[];
  sources: string[];
  whitelisted: boolean;
  whitelist_reason: string;
  links: Record<string, string>;
};

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.message || response.statusText);
  }
  return data as T;
}

export const api = {
  me: () => request<{ username: string; using_default_password: boolean }>("/api/auth/me"),
  login: (username: string, password: string) =>
    request("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  library: (params: Record<string, string>) => {
    const query = new URLSearchParams(params).toString();
    return request<{ items: MediaItem[]; stats: Record<string, number>; sync: { status: string; message: string } }>(
      `/api/library?${query}`
    );
  },
  sync: () => request("/api/sync", { method: "POST" }),
  syncStatus: () => request<{ status: string; message: string }>("/api/sync"),
  cleanup: (items: Partial<MediaItem>[], blacklist: boolean) =>
    request<{ results: { title: string; ok: boolean; error?: string }[] }>("/api/cleanup", {
      method: "POST",
      body: JSON.stringify({ items, delete_files: true, blacklist }),
    }),
  whitelist: () => request<{ items: WhitelistItem[] }>("/api/whitelist"),
  addWhitelist: (payload: Partial<WhitelistItem>) =>
    request("/api/whitelist", { method: "POST", body: JSON.stringify(payload) }),
  removeWhitelist: (id: number) => request(`/api/whitelist/${id}`, { method: "DELETE" }),
  settings: () => request<{ values: Record<string, string | boolean>; username: string; using_default_password: boolean }>("/api/settings"),
  saveSettings: (body: unknown) => request("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  test: (service: string) => request("/api/settings/test", { method: "POST", body: JSON.stringify({ service }) }),
};

export type WhitelistItem = {
  id: number;
  match_type: string;
  media_type: string;
  tmdb_id: number;
  pattern: string;
  note: string;
};
