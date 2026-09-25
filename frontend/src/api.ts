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
  radarr_4k_id: number | null;
  sonarr_id: number | null;
  seerr_media_id?: number | null;
  seerr_tmdb_id?: number;
  seerr_match_via?: string;
  requested_by: string;
  requested_at: string;
  last_watched_at: number | null;
  play_count: number;
  watcher_count: number;
  watchers: Watcher[];
  sources: string[];
  whitelisted: boolean;
  whitelist_reason: string;
  availability?: "downloaded" | "requested" | "partial";
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
  started_at?: number | null;
  finished_at?: number | null;
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

export type LogsResponse = {
  items: LogItem[];
  total: number;
  page: number;
  pages: number;
  levels: { info: number; warn: number; error: number };
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
  authStatus: () => request<{ setup_required: boolean }>("/api/auth/status"),
  setup: (username: string, password: string) =>
    request<{ username: string }>("/api/auth/setup", { method: "POST", body: JSON.stringify({ username, password }) }),
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
    return request<LogsResponse>(`/api/logs?${query}`);
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
      hide_settings: boolean;
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
  clearSeerr: (body: { ids?: number[]; all_stale?: boolean }) =>
    request<{ results: { title: string; ok: boolean; error?: string }[]; remaining: number }>("/api/unmatched/clear-seerr", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  addSeerr: (body: { ids?: number[]; all_missing?: boolean }) =>
    request<{ results: { title: string; ok: boolean; error?: string }[]; remaining: number }>("/api/unmatched/add-seerr", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  ignoreUnmatched: (ids: number[]) =>
    request<{ ignored: number; remaining: number }>("/api/unmatched/ignore", {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),
  ignoredUnmatched: () => request<{ items: IgnoredItem[] }>("/api/unmatched/ignored"),
  unignoreUnmatched: (id: number) => request<{ ok: boolean }>(`/api/unmatched/ignored/${id}`, { method: "DELETE" }),
  reviewMatches: () => request<{ items: ReviewMatch[] }>("/api/matches/review"),
  decideMatch: (id: number, action: "unlink" | "keep") =>
    request<{ ok: boolean; remaining: number }>(`/api/matches/${id}/${action}`, { method: "POST", body: JSON.stringify({}) }),
  matchDecisions: () => request<{ items: MatchDecision[] }>("/api/matches/decisions"),
  undoMatchDecision: (id: number) => request<{ ok: boolean }>(`/api/matches/decisions/${id}`, { method: "DELETE" }),
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
  account_username: string;
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
  tmdb_id: number;
  tvdb_id?: number;
  seerr_media_id?: number | null;
  requested_by: string;
  requested_at?: string;
  kind: string;
  seerr_state?: string;
  links: Record<string, string>;
};

export type IgnoredItem = {
  id: number;
  kind: string;
  media_type: string;
  tmdb_id: number;
  tvdb_id: number;
  title: string;
  reason: string;
  created_at: number;
};

export type ReviewMatch = {
  id: number;
  media_type: string;
  title: string;
  year: number | null;
  tmdb_id: number;
  seerr_title: string;
  seerr_tmdb_id: number;
  via: string;
  requested_by: string;
  requested_at: string;
};

export type MatchDecision = {
  id: number;
  media_type: string;
  seerr_tmdb_id: number;
  seerr_tvdb_id: number;
  library_tmdb_id: number;
  library_tvdb_id: number;
  seerr_title: string;
  library_title: string;
  reason: string;
  action: "unlink" | "keep";
  created_at: number;
};

export type ServiceTest = {
  service: string;
  ok: boolean;
  configured: boolean;
  message: string;
  detail?: string;
};

export type Maintenance = {
  cache_files: number;
  cache_bytes: number;
  library_count: number;
  people_count: number;
  unmatched_count: number;
};

export type WhitelistMatch = {
  id: number;
  title: string;
  year: number | null;
  media_type: string;
  tmdb_id: number;
};

export type WhitelistItem = {
  id: number;
  match_type: string;
  media_type: string;
  tmdb_id: number;
  pattern: string;
  note: string;
  matches?: WhitelistMatch[];
  match_count?: number;
};
