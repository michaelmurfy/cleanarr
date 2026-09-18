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
  art_url: string;
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
  rating: number | null;
  rating_votes: number;
  rating_source: string;
};

export type SyncStatus = {
  status: string;
  message: string;
  step?: string;
  current?: number;
  total?: number;
  percent?: number | null;
};

export type LogItem = {
  id: number;
  created_at: number;
  level: string;
  category: string;
  action: string;
  message: string;
  detail: unknown;
  actor: string;
};

export type LibraryResponse = {
  items: MediaItem[];
  stats: Record<string, number>;
  total: number;
  page: number;
  page_size: number;
  sync: SyncStatus;
};

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers as Record<string, string> | undefined) };
  if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    credentials: "include",
    ...options,
    headers,
  });
  if (response.status === 204) return {} as T;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : data.message || response.statusText);
  }
  return data as T;
}

export const api = {
  me: () => request<{ username: string; using_default_password: boolean }>("/api/auth/me"),
  login: (username: string, password: string) =>
    request<{ username: string }>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  library: (params: Record<string, string>) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value) query.set(key, value);
    }
    return request<LibraryResponse>(`/api/library?${query}`);
  },
  sync: () => request<SyncStatus>("/api/sync", { method: "POST" }),
  syncStatus: () => request<SyncStatus>("/api/sync"),
  logs: (params: Record<string, string>) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value) query.set(key, value);
    }
    return request<{ items: LogItem[]; total: number; page: number; pages: number }>(`/api/logs?${query}`);
  },
  cleanup: (items: Partial<MediaItem>[], blacklist: boolean) =>
    request<{ results: { title: string; ok: boolean; error?: string }[] }>("/api/cleanup", {
      method: "POST",
      body: JSON.stringify({ items, delete_files: true, blacklist }),
    }),
  whitelist: () => request<{ items: WhitelistItem[] }>("/api/whitelist"),
  addWhitelist: (payload: Partial<WhitelistItem>) =>
    request("/api/whitelist", { method: "POST", body: JSON.stringify(payload) }),
  removeWhitelist: (id: number) => request(`/api/whitelist/${id}`, { method: "DELETE" }),
  settings: () =>
    request<{
      values: Record<string, string | boolean>;
      locked: string[];
      env_file: boolean;
      username: string;
      username_locked: boolean;
      using_default_password: boolean;
      maintenance: Maintenance;
      sync: SyncStatus;
    }>("/api/settings"),
  saveSettings: (body: unknown) => request("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  test: (service: string) =>
    request<ServiceTest>("/api/settings/test", { method: "POST", body: JSON.stringify({ service }) }),
  testAll: () => request<{ results: ServiceTest[]; ok: boolean }>("/api/settings/test-all", { method: "POST" }),
  clearCache: () => request<{ removed: number; cache_files: number; cache_bytes: number }>("/api/settings/clear-cache", { method: "POST" }),
  clearLibrary: () =>
    request<{ media: number; people: number; unmatched: number; posters: number }>("/api/settings/clear-library", { method: "POST" }),
  unmatched: (params: Record<string, string>) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value) query.set(key, value);
    }
    return request<{ items: UnmatchedItem[]; total: number; page: number; pages: number; stats: Record<string, number>; sync: SyncStatus }>(
      `/api/unmatched?${query}`,
    );
  },
  users: (params: Record<string, string>) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value) query.set(key, value);
    }
    return request<{ items: Person[]; stats: Record<string, number> }>(`/api/users?${query}`);
  },
};

export type Person = {
  canonical: string;
  display_name: string;
  plex_username: string;
  email: string;
  aliases: string[];
  request_count: number;
  library_count: number;
  library_size: number;
  play_count: number;
  last_watched_at: number | null;
  sources: string[];
  links: Record<string, string>;
  matched: boolean;
};

export type UnmatchedItem = {
  id: number;
  source: string;
  media_type: string;
  title: string;
  year: number;
  plays: number;
  reason: string;
  links: Record<string, string>;
};

export type ServiceTest = {
  service: string;
  ok: boolean;
  configured: boolean;
  message: string;
};

export type Maintenance = {
  cache_files: number;
  cache_bytes: number;
  library_count: number;
  people_count: number;
  unmatched_count: number;
};

export type WhitelistItem = {
  id: number;
  match_type: string;
  media_type: string;
  tmdb_id: number;
  pattern: string;
  note: string;
};
