import { FormEvent, MouseEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, LogItem, MediaItem, Person, ServiceTest, SyncStatus, UnmatchedItem, WhitelistItem } from "./api";
import { Brand } from "./Logo";

type Page = "library" | "unmatched" | "users" | "whitelist" | "logs" | "settings";

const FILTERS_KEY = "cleanarr.library";

// Sent from their own form state below, so the service-settings loop must not resend
// the values loaded from the API over the top of them.
const APP_SETTING_KEYS = [
  "sync_schedule_enabled",
  "sync_interval_hours",
  "auto_delete_enabled",
  "auto_delete_max_per_run",
  "auto_delete_stale_days",
];

type Filters = {
  q: string;
  mediaType: string;
  watched: string;
  sort: string;
  staleDays: string;
  maxRating: string;
  page: number;
  pageSize: string;
};

const defaultFilters: Filters = {
  q: "",
  mediaType: "",
  watched: "",
  sort: "oldest",
  staleDays: "365",
  maxRating: "",
  page: 1,
  pageSize: "50",
};

function loadFilters(): Filters {
  try {
    const raw = sessionStorage.getItem(FILTERS_KEY);
    return raw ? { ...defaultFilters, ...JSON.parse(raw) } : defaultFilters;
  } catch {
    return defaultFilters;
  }
}

function bytes(value: number) {
  if (!value) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let i = 0;
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024;
    i += 1;
  }
  return `${size.toFixed(size >= 10 || i < 2 ? 0 : 1)} ${units[i]}`;
}

function when(ts: number | null) {
  if (!ts) return "Never";
  const diff = Date.now() / 1000 - ts;
  if (diff < 3600) return "Just now";
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)}d ago`;
  if (diff < 86400 * 365) return `${Math.floor(diff / 30 / 86400)}mo ago`;
  const years = Math.floor(diff / 365 / 86400);
  return `${years}y ago`;
}

function whenFull(ts: number | null) {
  return ts ? new Date(ts * 1000).toLocaleString() : "Never watched";
}

function parseStamp(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  if (typeof value === "number") return value > 1_000_000_000_000 ? Math.floor(value / 1000) : value;
  const text = String(value).trim();
  if (!text) return null;
  if (/^\d+$/.test(text)) {
    const n = Number(text);
    return n > 1_000_000_000_000 ? Math.floor(n / 1000) : n;
  }
  const ms = Date.parse(text);
  return Number.isNaN(ms) ? null : Math.floor(ms / 1000);
}

function availabilityLabel(value?: string | null) {
  if (value === "requested") return "Requested";
  if (value === "partial") return "Partial";
  return "";
}

function Requester({ name, at }: { name?: string | null; at?: string | number | null }) {
  if (!name) return <span className="muted">—</span>;
  const ts = parseStamp(at);
  return (
    <div className="requester">
      <div className="requester-name">{name}</div>
      {ts ? (
        <div className="requester-when" title={new Date(ts * 1000).toLocaleString()}>
          Requested {when(ts)}
        </div>
      ) : null}
    </div>
  );
}

function isInteractive(event: MouseEvent) {
  return Boolean((event.target as HTMLElement).closest("a, button, input, label"));
}

const SERVICE_META: Record<string, { label: string; className: string }> = {
  seerr: { label: "Seerr", className: "seerr" },
  tautulli: { label: "Tautulli", className: "tautulli" },
  tracearr: { label: "Tracearr", className: "tracearr" },
  jellystat: { label: "Jellystat", className: "jellystat" },
  radarr: { label: "Radarr", className: "radarr" },
  sonarr: { label: "Sonarr", className: "sonarr" },
};

function ServiceLinks({ links }: { links?: Record<string, string> }) {
  const entries = Object.entries(SERVICE_META).filter(([key]) => links?.[key]);
  if (!entries.length) return <span className="muted">—</span>;
  return (
    <div className="service-links">
      {entries.map(([key, meta]) => (
        <a
          key={key}
          className={`service-pill ${meta.className}`}
          href={links?.[key]}
          target="_blank"
          rel="noreferrer"
          title={`Open in ${meta.label}`}
        >
          {meta.label}
        </a>
      ))}
    </div>
  );
}

export function App() {
  const [user, setUser] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);

  useEffect(() => {
    api.me().then((me) => setUser(me.username)).catch(() => setUser(null)).finally(() => setBooting(false));
  }, []);

  if (booting) {
    return (
      <div className="login">
        <div className="login-card"><Brand /><p className="muted">Loading…</p></div>
      </div>
    );
  }
  if (!user) return <Login onDone={setUser} />;
  return <Shell user={user} onLogout={() => setUser(null)} />;
}

function Login({ onDone }: { onDone: (user: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const result = await api.login(username, password);
      onDone(result.username);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    }
  }

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <Brand />
        <h1>Sign in</h1>
        <p className="muted">Find the titles nobody watches, keep the ones that matter.</p>
        <div className="stack">
          <label>Username
            <input
              name="username"
              value={username}
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              autoFocus
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label>Password
            <input
              type="password"
              name="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
        </div>
        {error && <p className="error">{error}</p>}
        <button className="primary" type="submit">Continue</button>
      </form>
    </div>
  );
}

function Shell({ user, onLogout }: { user: string; onLogout: () => void }) {
  const [page, setPage] = useState<Page>("library");
  const [sync, setSync] = useState<SyncStatus>({ status: "idle", message: "" });
  const [unmatchedCount, setUnmatchedCount] = useState(0);
  const prevSync = useRef(sync.status);

  function refreshUnmatched() {
    api.unmatched({ page_size: "1" }).then((data) => {
      setUnmatchedCount(data.stats.count || 0);
    }).catch(() => undefined);
  }

  useEffect(() => {
    api.syncStatus().then(setSync).catch(() => undefined);
    refreshUnmatched();
  }, []);

  useEffect(() => {
    if (!unmatchedCount && page === "unmatched") setPage("library");
  }, [unmatchedCount, page]);

  useEffect(() => {
    if (sync.status !== "running") return;
    const timer = setInterval(() => {
      api.syncStatus().then(setSync).catch(() => undefined);
    }, 800);
    return () => clearInterval(timer);
  }, [sync.status]);

  useEffect(() => {
    if (prevSync.current === "running" && sync.status !== "running") refreshUnmatched();
    prevSync.current = sync.status;
  }, [sync.status]);

  return (
    <div className="shell">
      <header className="topbar">
        <Brand compact />
        <nav className="nav">
          <button className={page === "library" ? "active" : ""} onClick={() => setPage("library")}>Library</button>
          {unmatchedCount > 0 && (
            <button className={`alert ${page === "unmatched" ? "active" : ""}`} onClick={() => setPage("unmatched")}>
              Unmatched<span className="nav-count">{unmatchedCount}</span>
            </button>
          )}
          <button className={page === "users" ? "active" : ""} onClick={() => setPage("users")}>Users</button>
          <button className={page === "whitelist" ? "active" : ""} onClick={() => setPage("whitelist")}>Whitelist</button>
          <button className={page === "logs" ? "active" : ""} onClick={() => setPage("logs")}>Logs</button>
          <button className={page === "settings" ? "active" : ""} onClick={() => setPage("settings")}>Settings</button>
        </nav>
        <div className="spacer" />
        <div className="topbar-sync">
          <span className={`sync-status ${sync.status === "running" ? "live" : ""}`} title={sync.message || "Idle"}>
            {sync.status === "running" && <span className="spinner" aria-hidden="true" />}
            <span className="sync-copy">
              {sync.status === "running" ? (sync.message || "Syncing…") : (sync.message || "Idle")}
              {sync.status === "running" && sync.percent != null && sync.total ? ` · ${sync.percent}%` : ""}
            </span>
          </span>
          <button
            className="primary"
            disabled={sync.status === "running"}
            onClick={async () => { setSync(await api.sync()); }}
          >
            {sync.status === "running" ? "Syncing…" : "Sync now"}
          </button>
        </div>
        <span className="muted">{user}</span>
        <button className="ghost" onClick={async () => { await api.logout(); onLogout(); }}>Sign out</button>
      </header>
      {page === "library" && <Library sync={sync} setSync={setSync} unmatchedCount={unmatchedCount} onOpenUnmatched={() => setPage("unmatched")} onUnmatchedCount={setUnmatchedCount} />}
      {page === "unmatched" && unmatchedCount > 0 && (
        <Unmatched sync={sync} setSync={setSync} onUnmatchedCount={(count) => {
          setUnmatchedCount(count);
          if (!count) setPage("library");
        }} />
      )}
      {page === "users" && <Users onOpenLibrary={(q) => {
        sessionStorage.setItem(FILTERS_KEY, JSON.stringify({ ...defaultFilters, q }));
        setPage("library");
      }} />}
      {page === "whitelist" && <Whitelist />}
      {page === "logs" && <Logs sync={sync} />}
      {page === "settings" && <Settings />}
    </div>
  );
}

function Library({
  sync,
  setSync,
  unmatchedCount,
  onOpenUnmatched,
  onUnmatchedCount,
}: {
  sync: SyncStatus;
  setSync: (value: SyncStatus) => void;
  unmatchedCount: number;
  onOpenUnmatched: () => void;
  onUnmatchedCount: (count: number) => void;
}) {
  const [filters, setFilters] = useState<Filters>(loadFilters);
  const [qInput, setQInput] = useState(filters.q);
  const [items, setItems] = useState<MediaItem[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const [pending, setPending] = useState<null | { blacklist: boolean }>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const prevSync = useRef(sync.status);

  useEffect(() => {
    sessionStorage.setItem(FILTERS_KEY, JSON.stringify(filters));
  }, [filters]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setFilters((current) => current.q === qInput ? current : { ...current, q: qInput, page: 1 });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [qInput]);

  async function load(next = filters) {
    setLoading(true);
    try {
      const data = await api.library({
        q: next.q,
        media_type: next.mediaType,
        watched: next.watched,
        sort: next.sort,
        page: String(next.page),
        page_size: next.pageSize,
        stale_days: next.staleDays,
        max_rating: next.maxRating,
      });
      const pages = data.stats.pages || 1;
      if (next.page > pages) {
        setFilters((current) => ({ ...current, page: pages }));
        return;
      }
      setItems(data.items);
      setStats(data.stats);
      setSync(data.sync);
      onUnmatchedCount(data.stats.unmatched || 0);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, [filters.q, filters.mediaType, filters.watched, filters.sort, filters.page, filters.pageSize, filters.staleDays, filters.maxRating]);

  useEffect(() => {
    setSelected(new Set());
  }, [filters.q, filters.mediaType, filters.watched, filters.sort, filters.page, filters.pageSize, filters.staleDays, filters.maxRating]);

  useEffect(() => {
    if (prevSync.current === "running" && sync.status !== "running") {
      load().catch(() => undefined);
    }
    prevSync.current = sync.status;
  }, [sync.status]);

  const selectedItems = useMemo(() => items.filter((item) => selected.has(item.id)), [items, selected]);
  const selectedSize = selectedItems.reduce((sum, item) => sum + (item.size_bytes || 0), 0);

  function patch(partial: Partial<Filters>) {
    setFilters((current) => ({ ...current, ...partial, page: partial.page ?? 1 }));
  }

  function toggle(id: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function confirm() {
    if (!pending) return;
    setBusy(true);
    setError("");
    try {
      const payload = selectedItems.map((item) => ({
        media_type: item.media_type,
        tmdb_id: item.tmdb_id,
        tvdb_id: item.tvdb_id,
        title: item.title,
      }));
      const result = await api.cleanup(payload, pending.blacklist);
      const failed = result.results.filter((row) => !row.ok);
      if (failed.length) setError(failed.map((row) => `${row.title}: ${row.error}`).join(" · "));
      setSelected(new Set());
      setPending(null);
      await load(filters);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cleanup failed");
    } finally {
      setBusy(false);
    }
  }

  async function keep(item: MediaItem) {
    setError("");
    try {
      await api.addWhitelist({
        match_type: item.tmdb_id ? "id" : "title",
        media_type: item.media_type,
        tmdb_id: item.tmdb_id || 0,
        pattern: item.title,
        note: "Kept from library",
      });
      await load(filters);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not whitelist");
    }
  }

  const pages = stats.pages || 1;

  return (
    <div className="page">
      <div className="stats">
        <button className={`stat ${!filters.watched && !filters.maxRating ? "active" : ""}`} onClick={() => patch({ watched: "", maxRating: "" })}>
          <span className="muted">Matching titles</span><b>{stats.count ?? 0}</b>
        </button>
        <button className={`stat never ${filters.watched === "never" ? "active" : ""}`} onClick={() => patch(filters.watched === "never" ? { watched: "" } : { watched: "never", sort: "size" })}>
          <span className="muted">Never watched</span><b>{stats.never_watched ?? 0}</b>
        </button>
        <button className={`stat stale ${filters.watched === "stale" ? "active" : ""}`} onClick={() => patch(filters.watched === "stale" ? { watched: "" } : { watched: "stale", sort: "oldest" })}>
          <span className="muted">Stale / unwatched</span><b>{stats.stale ?? 0}</b>
        </button>
        <button className={`stat pending ${filters.watched === "requested" ? "active" : ""}`} onClick={() => patch(filters.watched === "requested" ? { watched: "" } : { watched: "requested", maxRating: "", sort: "title" })}>
          <span className="muted">Requested</span><b>{stats.requested ?? 0}</b>
        </button>
        <button className={`stat ok ${filters.watched === "protected" ? "active" : ""}`} onClick={() => patch(filters.watched === "protected" ? { watched: "" } : { watched: "protected", maxRating: "", sort: "title" })}>
          <span className="muted">Protected</span><b>{stats.whitelisted ?? 0}</b>
        </button>
        {unmatchedCount > 0 && (
          <button className="stat warn" onClick={onOpenUnmatched}>
            <span className="muted">Unmatched</span><b>{unmatchedCount}</b>
          </button>
        )}
      </div>
      <div className="filters">
        <button className={`chip-btn ${filters.watched === "never" ? "active" : ""}`} onClick={() => patch(filters.watched === "never" ? { watched: "" } : { watched: "never", sort: "size" })}>Never watched</button>
        <button className={`chip-btn ${filters.watched === "stale" && filters.sort === "oldest" ? "active" : ""}`} onClick={() => patch(filters.watched === "stale" ? { watched: "" } : { watched: "stale", sort: "oldest" })}>Oldest / stale</button>
        <button className={`chip-btn pending ${filters.watched === "requested" ? "active" : ""}`} onClick={() => patch(filters.watched === "requested" ? { watched: "" } : { watched: "requested", maxRating: "", sort: "title" })}>Requested</button>
        <button className={`chip-btn ok ${filters.watched === "protected" ? "active" : ""}`} onClick={() => patch(filters.watched === "protected" ? { watched: "" } : { watched: "protected", maxRating: "", sort: "title" })}>Protected</button>
        <button className={`chip-btn ${filters.sort === "rating" ? "active" : ""}`} onClick={() => patch({ sort: filters.sort === "rating" ? "oldest" : "rating" })}>Lowest rated</button>
        <button className={`chip-btn ${filters.sort === "size" ? "active" : ""}`} onClick={() => patch({ sort: filters.sort === "size" ? "oldest" : "size" })}>Largest</button>
      </div>
      <div className="filters">
        <input type="search" placeholder="Search title, requester, watcher, requested" value={qInput} onChange={(e) => setQInput(e.target.value)} />
        <select value={filters.mediaType} onChange={(e) => patch({ mediaType: e.target.value })}>
          <option value="">Movies & TV</option>
          <option value="movie">Movies</option>
          <option value="tv">TV</option>
        </select>
        <select value={filters.watched} onChange={(e) => patch({ watched: e.target.value })}>
          <option value="">Any watch state</option>
          <option value="never">Never watched</option>
          <option value="stale">Stale / unwatched</option>
          <option value="watched">Watched</option>
          <option value="requested">Requested</option>
          <option value="protected">Protected</option>
        </select>
        <select value={filters.staleDays} onChange={(e) => patch({ staleDays: e.target.value })}>
          <option value="90">Stale after 90 days</option>
          <option value="180">Stale after 6 months</option>
          <option value="365">Stale after 1 year</option>
          <option value="730">Stale after 2 years</option>
        </select>
        <select value={filters.maxRating} onChange={(e) => patch({ maxRating: e.target.value, sort: e.target.value ? "rating" : filters.sort })}>
          <option value="">Any rating</option>
          <option value="5">Rated 5 or below</option>
          <option value="6">Rated 6 or below</option>
          <option value="7">Rated 7 or below</option>
        </select>
        <select value={filters.sort} onChange={(e) => patch({ sort: e.target.value })}>
          <option value="oldest">Oldest first</option>
          <option value="last_watched">Recently watched</option>
          <option value="rating">Lowest rating</option>
          <option value="plays">Play count</option>
          <option value="size">Size</option>
          <option value="title">Title</option>
          <option value="requested">Requested by</option>
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th className="tick-cell">
                <input
                  className="tick"
                  type="checkbox"
                  checked={items.length > 0 && items.every((item) => item.whitelisted || selected.has(item.id)) && items.some((item) => !item.whitelisted)}
                  onChange={(e) => setSelected(e.target.checked ? new Set(items.filter((item) => !item.whitelisted).map((item) => item.id)) : new Set())}
                />
              </th>
              <th>Title</th>
              <th>Rating</th>
              <th>Last watched</th>
              <th>Plays</th>
              <th>Watchers</th>
              <th>Requested by</th>
              <th>Size</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={item.id}
                className={`${item.whitelisted ? "is-protected" : "clickable"} ${selected.has(item.id) ? "selected" : ""}`}
                onClick={(event) => {
                  if (item.whitelisted || isInteractive(event)) return;
                  toggle(item.id);
                }}
              >
                <td className="tick-cell">
                  <input className="tick" type="checkbox" disabled={item.whitelisted} checked={selected.has(item.id)} onChange={() => toggle(item.id)} />
                </td>
                <td>
                  <div className="title-cell">
                    {item.art_url ? <img className="poster" src={item.art_url} alt="" /> : <div className="poster placeholder">No art</div>}
                    <div>
                      <strong>{item.title}</strong> {item.year ? <span className="muted">({item.year})</span> : null}
                      <div className="title-meta">
                        <span className={`type-chip ${item.media_type}`}>{item.media_type === "movie" ? "Movie" : "TV"}</span>
                        {availabilityLabel(item.availability) ? <span className={`chip ${item.availability === "requested" ? "pending" : "partial"}`}>{availabilityLabel(item.availability)}</span> : null}
                        {item.sources.length ? <span className="muted">{item.sources.join(" / ")}</span> : null}
                        {item.whitelisted
                          ? <span className="chip ok" title={item.whitelist_reason}>Protected · {item.whitelist_reason}</span>
                          : <button type="button" className="keep-btn" onClick={() => keep(item)}>Whitelist</button>}
                      </div>
                    </div>
                  </div>
                </td>
                <td className="rating" title={item.rating_source ? `${item.rating_source} · ${item.rating_votes} votes` : "No rating"}>
                  {item.rating != null ? <><strong>{Number(item.rating).toFixed(1)}</strong> <span className="muted">/10</span></> : "—"}
                </td>
                <td title={item.availability === "requested" ? "Requested, not downloaded yet" : whenFull(item.last_watched_at)}>
                  {item.availability === "requested" ? "—" : when(item.last_watched_at)}
                </td>
                <td>{item.play_count}</td>
                <td className="watchers" title={item.watchers.map((watcher) => `${watcher.user} ×${watcher.plays}`).join(", ")}>
                  {item.watchers.length
                    ? `${item.watchers.slice(0, 2).map((watcher) => `${watcher.user} ×${watcher.plays}`).join(", ")}${item.watchers.length > 2 ? ` +${item.watchers.length - 2}` : ""}`
                    : "—"}
                </td>
                <td><Requester name={item.requested_by} at={item.requested_at} /></td>
                <td>{bytes(item.size_bytes)}</td>
                <td>
                  <div className="row-actions">
                    <ServiceLinks links={item.links} />
                  </div>
                </td>
              </tr>
            ))}
            {!items.length && (
              <tr>
                <td colSpan={9} className="empty">{loading ? "Loading library…" : filters.watched === "protected" ? "No library titles match the current whitelist." : filters.watched === "requested" ? "Nothing is sitting in a requested / not-downloaded state." : "Nothing matches these filters. Try Never watched or Oldest / stale."}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <span className="muted">{stats.count ?? 0} titles · {bytes(stats.size_bytes || 0)}</span>
        <div className="spacer" />
        <select value={filters.pageSize} onChange={(e) => patch({ pageSize: e.target.value })}>
          <option value="25">25 / page</option>
          <option value="50">50 / page</option>
          <option value="100">100 / page</option>
        </select>
        <span className="muted">Page {stats.page || 1} of {pages}</span>
        <button className="ghost" disabled={(stats.page || 1) <= 1} onClick={() => setFilters((current) => ({ ...current, page: current.page - 1 }))}>Previous</button>
        <button className="ghost" disabled={(stats.page || 1) >= pages} onClick={() => setFilters((current) => ({ ...current, page: current.page + 1 }))}>Next</button>
      </div>
      {selected.size > 0 && (
        <div className="bulk">
          <strong>{selected.size} selected</strong>
          <span className="muted">{bytes(selectedSize)}</span>
          <div className="spacer" />
          <button className="ghost" onClick={() => setPending({ blacklist: true })}>Ban in Seerr + delete</button>
          <button className="danger" onClick={() => setPending({ blacklist: false })}>Delete from disk</button>
        </div>
      )}
      {pending && (
        <div className="modal-back" onClick={() => !busy && setPending(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>{pending.blacklist ? "Ban and delete" : "Delete from disk"}</h2>
            <p>
              This removes {selectedItems.length} title{selectedItems.length === 1 ? "" : "s"} from Radarr/Sonarr
              {pending.blacklist ? ", blacklists them in Seerr," : ""} and deletes the files. Whitelisted titles are skipped. Filters stay where they are.
            </p>
            <ul>
              {selectedItems.slice(0, 8).map((item) => <li key={item.id}>{item.title}</li>)}
              {selectedItems.length > 8 && <li>…and {selectedItems.length - 8} more</li>}
            </ul>
            <div className="filters">
              <button className="ghost" disabled={busy} onClick={() => setPending(null)}>Cancel</button>
              <button className="danger" disabled={busy} onClick={confirm}>{busy ? "Working…" : "Confirm"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Unmatched({
  sync,
  setSync,
  onUnmatchedCount,
}: {
  sync: SyncStatus;
  setSync: (value: SyncStatus) => void;
  onUnmatchedCount: (count: number) => void;
}) {
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("seerr_missing");
  const [mediaType, setMediaType] = useState("");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<UnmatchedItem[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<null | { all: boolean; ids: number[] }>(null);
  const prevSync = useRef(sync.status);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQ(qInput);
      setPage(1);
    }, 200);
    return () => window.clearTimeout(timer);
  }, [qInput]);

  async function load(nextPage = page) {
    setLoading(true);
    try {
      const data = await api.unmatched({
        q,
        kind,
        media_type: mediaType,
        page: String(nextPage),
        page_size: "50",
      });
      setItems(data.items);
      setStats(data.stats);
      setTotal(data.total);
      setPages(data.pages || 1);
      setSync(data.sync);
      onUnmatchedCount(data.stats.count || 0);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(page).catch(() => undefined);
  }, [q, kind, mediaType, page]);

  useEffect(() => {
    setSelected(new Set());
  }, [q, kind, mediaType, page]);

  useEffect(() => {
    if (prevSync.current === "running" && sync.status !== "running") {
      load(page).catch(() => undefined);
    }
    prevSync.current = sync.status;
  }, [sync.status]);

  const staleItems = items.filter((item) => item.kind === "seerr_missing");
  const staleCount = stats.seerr_missing ?? 0;

  function toggle(id: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function confirm() {
    if (!pending) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.clearSeerr(pending.all ? { all_stale: true } : { ids: pending.ids });
      const failed = result.results.filter((row) => !row.ok);
      if (failed.length) setError(failed.map((row) => `${row.title}: ${row.error}`).join(" · "));
      setSelected(new Set());
      setPending(null);
      onUnmatchedCount(result.remaining);
      if (result.remaining) await load(page);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not clear Seerr records");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <h2>Unmatched</h2>
      <p className="muted">
        Gaps between Radarr/Sonarr and Seerr. Clear stale Seerr records so those titles can be requested again.
      </p>
      <div className="stats">
        <button className={`stat ${!kind ? "active" : ""}`} onClick={() => { setKind(""); setPage(1); }}>
          <span className="muted">All gaps</span><b>{stats.count ?? total}</b>
        </button>
        <button className={`stat warn ${kind === "seerr_missing" ? "active" : ""}`} onClick={() => { setKind(kind === "seerr_missing" ? "" : "seerr_missing"); setPage(1); }}>
          <span className="muted">Stale in Seerr</span><b>{staleCount}</b>
        </button>
        <button className={`stat ${kind === "no_seerr" ? "active" : ""}`} onClick={() => { setKind(kind === "no_seerr" ? "" : "no_seerr"); setPage(1); }}>
          <span className="muted">Not in Seerr</span><b>{stats.no_seerr ?? 0}</b>
        </button>
        <div className="stat" style={{ cursor: "default" }}>
          <span className="muted">Library</span>
          <b>{(stats.radarr_count ?? 0) + (stats.sonarr_count ?? 0)}</b>
        </div>
      </div>
      <div className="filters">
        <input type="search" placeholder="Search unmatched titles" value={qInput} onChange={(e) => setQInput(e.target.value)} />
        <select value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }}>
          <option value="">All gaps</option>
          <option value="seerr_missing">Stale in Seerr</option>
          <option value="no_seerr">Library not in Seerr</option>
        </select>
        <select value={mediaType} onChange={(e) => { setMediaType(e.target.value); setPage(1); }}>
          <option value="">Movies & TV</option>
          <option value="movie">Movies</option>
          <option value="tv">TV</option>
        </select>
        <div className="spacer" />
        {staleCount > 0 && (
          <button className="danger" type="button" onClick={() => setPending({ all: true, ids: [] })}>Clear all stale Seerr</button>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th className="tick-cell">
                <input
                  className="tick"
                  type="checkbox"
                  checked={staleItems.length > 0 && staleItems.every((item) => selected.has(item.id))}
                  disabled={!staleItems.length}
                  onChange={(e) => setSelected(e.target.checked ? new Set(staleItems.map((item) => item.id)) : new Set())}
                />
              </th>
              <th>Title</th>
              <th>Where</th>
              <th>Type</th>
              <th>Requested by</th>
              <th>Why</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const stale = item.kind === "seerr_missing";
              return (
                <tr
                  key={item.id}
                  className={`unmatched-row ${stale ? "clickable" : ""} ${selected.has(item.id) ? "selected" : ""}`}
                  onClick={(event) => {
                    if (!stale || isInteractive(event)) return;
                    toggle(item.id);
                  }}
                >
                  <td className="tick-cell">
                    <input className="tick" type="checkbox" disabled={!stale} checked={selected.has(item.id)} onChange={() => toggle(item.id)} />
                  </td>
                  <td>
                    <strong>{item.title}</strong> {item.year ? <span className="muted">({item.year})</span> : null}
                    <div className="title-meta">
                      <span className={`type-chip ${item.media_type}`}>{item.media_type === "tv" ? "TV" : "Movie"}</span>
                      <span className="chip warn">{stale ? "Not in library" : "Not in Seerr"}</span>
                    </div>
                  </td>
                  <td className="capitalize">{item.source}</td>
                  <td>{item.media_type === "tv" ? "TV" : "Movie"}</td>
                  <td><Requester name={item.requested_by} at={item.requested_at} /></td>
                  <td className="muted">{item.reason}</td>
                  <td>
                    <div className="row-actions">
                      {stale && <button className="danger-ghost" type="button" onClick={() => setPending({ all: false, ids: [item.id] })}>Clear in Seerr</button>}
                      <ServiceLinks links={item.links} />
                    </div>
                  </td>
                </tr>
              );
            })}
            {!items.length && (
              <tr>
                <td colSpan={7} className="empty">
                  {loading ? "Loading unmatched titles…" : "Radarr, Sonarr, and Seerr agree on the current library."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <span className="muted">{total} unmatched</span>
        <div className="spacer" />
        <span className="muted">Page {page} of {pages}</span>
        <button className="ghost" disabled={page <= 1} onClick={() => setPage((current) => current - 1)}>Previous</button>
        <button className="ghost" disabled={page >= pages} onClick={() => setPage((current) => current + 1)}>Next</button>
      </div>
      {selected.size > 0 && (
        <div className="bulk">
          <strong>{selected.size} selected</strong>
          <span className="muted">Remove stale Seerr media so they can be requested again</span>
          <div className="spacer" />
          <button className="danger" onClick={() => setPending({ all: false, ids: [...selected] })}>Clear in Seerr</button>
        </div>
      )}
      {pending && (
        <div className="modal-back" onClick={() => !busy && setPending(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>{pending.all ? "Clear all stale Seerr records" : "Clear in Seerr"}</h2>
            <p>
              This deletes the stale media records in Seerr so people can request them again. Radarr and Sonarr are not touched.
            </p>
            <ul>
              {(pending.all ? staleItems : items.filter((item) => pending.ids.includes(item.id))).slice(0, 8).map((item) => <li key={item.id}>{item.title}</li>)}
              {pending.all && staleCount > staleItems.length && <li>…and {staleCount - staleItems.length} more</li>}
              {!pending.all && pending.ids.length > 8 && <li>…and {pending.ids.length - 8} more</li>}
            </ul>
            <div className="filters">
              <button className="ghost" disabled={busy} onClick={() => setPending(null)}>Cancel</button>
              <button className="danger" disabled={busy} onClick={confirm}>{busy ? "Clearing…" : "Confirm"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Logs({ sync }: { sync: SyncStatus }) {
  const [items, setItems] = useState<LogItem[]>([]);
  const [category, setCategory] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);

  async function load(nextPage = page) {
    const data = await api.logs({
      q,
      category,
      page: String(nextPage),
      page_size: "80",
    });
    setItems(data.items);
    setPages(data.pages || 1);
    setTotal(data.total || 0);
    setPage(data.page || nextPage);
  }

  useEffect(() => {
    load(1).catch(() => undefined);
  }, [category, q]);

  useEffect(() => {
    if (sync.status !== "running") return;
    const timer = setInterval(() => load(page).catch(() => undefined), 1500);
    return () => clearInterval(timer);
  }, [sync.status, page, category, q]);

  return (
    <div className="page">
      <h2>Logs</h2>
      <p className="muted">Sync progress, matching, and deletions. Newest first.</p>
      <div className="filters">
        <input type="search" placeholder="Search logs" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>
          <option value="sync">Sync</option>
          <option value="match">Matching</option>
          <option value="audit">Audit</option>
          <option value="system">System</option>
        </select>
        <div className="spacer" />
        <span className="muted">{total} entries</span>
      </div>
      <div className="log-list">
        {items.map((item) => (
          <div className={`log-row ${item.level}`} key={item.id}>
            <span className={`log-level ${item.level}`}>{item.level}</span>
            <div>
              <div>{item.message}</div>
              <div className="muted">
                {new Date(item.created_at * 1000).toLocaleString()}
                {item.category ? ` · ${item.category}` : ""}
                {item.action ? ` · ${item.action}` : ""}
                {item.actor ? ` · ${item.actor}` : ""}
              </div>
              {Array.isArray((item.detail as { titles?: string[] } | null)?.titles) && (
                <ul className="log-titles">
                  {((item.detail as { titles: string[] }).titles || []).slice(0, 20).map((title) => (
                    <li key={title}>{title}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ))}
        {!items.length && <p className="muted">No log entries yet. Run a sync to populate this.</p>}
      </div>
      <div className="pager">
        <button className="ghost" disabled={page <= 1} onClick={() => load(page - 1)}>Previous</button>
        <span className="muted">Page {page} of {pages}</span>
        <button className="ghost" disabled={page >= pages} onClick={() => load(page + 1)}>Next</button>
      </div>
    </div>
  );
}

function Users({ onOpenLibrary }: { onOpenLibrary: (q: string) => void }) {
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("requests");
  const [onlyUnmatched, setOnlyUnmatched] = useState(false);
  const [items, setItems] = useState<Person[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});

  useEffect(() => {
    const timer = window.setTimeout(() => setQ(qInput), 200);
    return () => window.clearTimeout(timer);
  }, [qInput]);

  useEffect(() => {
    api.users({ q, sort }).then((data) => {
      setItems(data.items);
      setStats(data.stats);
    }).catch(() => undefined);
  }, [q, sort]);

  const visible = onlyUnmatched ? items.filter((person) => !person.matched) : items;

  return (
    <div className="page">
      <h2>Users</h2>
      <p className="muted">Seerr requesters and Tautulli/Tracearr/Jellystat watchers are matched to Plex usernames when those exist.</p>
      <div className="stats">
        <div className="stat" style={{ cursor: "default" }}><span className="muted">People</span><b>{stats.users ?? 0}</b></div>
        <div className="stat" style={{ cursor: "default" }}><span className="muted">Requests in library</span><b>{stats.requests ?? 0}</b></div>
        <div className="stat" style={{ cursor: "default" }}><span className="muted">Plays</span><b>{stats.plays ?? 0}</b></div>
        <button className={`stat warn ${onlyUnmatched ? "active" : ""}`} onClick={() => setOnlyUnmatched((current) => !current)}>
          <span className="muted">Unmatched to Plex</span><b>{stats.unmatched ?? 0}</b>
        </button>
      </div>
      <div className="filters">
        <input type="search" placeholder="Search name, Plex user, email" value={qInput} onChange={(e) => setQInput(e.target.value)} />
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="requests">Most requests</option>
          <option value="library">Most library items</option>
          <option value="plays">Most plays</option>
          <option value="size">Largest requested</option>
          <option value="name">Name</option>
        </select>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>User</th>
              <th>Requests</th>
              <th>In library</th>
              <th>Plays</th>
              <th>Requested size</th>
              <th>Last watched</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {visible.map((person) => (
              <tr key={person.canonical} className={person.matched ? "" : "unmatched-row"}>
                <td>
                  <strong>{person.display_name}</strong>
                  <div className="muted">
                    {person.plex_username && person.plex_username !== person.display_name ? `Plex · ${person.plex_username}` : person.plex_username ? "Plex user" : "No Plex username"}
                    {person.email ? ` · ${person.email}` : ""}
                  </div>
                  {!person.matched && <span className="chip warn">Unmatched</span>}
                </td>
                <td>{person.request_count}</td>
                <td>{person.library_count}</td>
                <td>{person.play_count}</td>
                <td>{bytes(person.library_size)}</td>
                <td title={whenFull(person.last_watched_at)}>{when(person.last_watched_at)}</td>
                <td>
                  <div className="row-actions">
                    <button className="ghost" onClick={() => onOpenLibrary(person.display_name)}>Library</button>
                    <ServiceLinks links={person.links} />
                  </div>
                </td>
              </tr>
            ))}
            {!visible.length && (
              <tr><td colSpan={7} className="empty">{onlyUnmatched ? "Every listed person matched a Plex username." : "No users yet. Sync the library to pull Seerr, Tautulli, Tracearr, and Jellystat people."}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Whitelist() {
  const [items, setItems] = useState<WhitelistItem[]>([]);
  const [pattern, setPattern] = useState("");
  const [note, setNote] = useState("");
  const [matchType, setMatchType] = useState("title");
  const [mediaType, setMediaType] = useState("any");

  async function load() {
    setItems((await api.whitelist()).items);
  }
  useEffect(() => { load().catch(() => undefined); }, []);

  async function add(event: FormEvent) {
    event.preventDefault();
    await api.addWhitelist({ pattern, note, match_type: matchType, media_type: mediaType, tmdb_id: matchType === "id" ? Number(pattern) : 0 });
    setPattern("");
    setNote("");
    await load();
  }

  return (
    <div className="page">
      <h2>Whitelist</h2>
      <p className="muted">Title matches are case-insensitive substrings. “Stargate” or “Back to the Future” protects the franchise. You can also whitelist a title from the library list.</p>
      <form className="filters" onSubmit={add}>
        <select value={matchType} onChange={(e) => setMatchType(e.target.value)}>
          <option value="title">Title contains</option>
          <option value="id">TMDB id</option>
        </select>
        <select value={mediaType} onChange={(e) => setMediaType(e.target.value)}>
          <option value="any">Any type</option>
          <option value="movie">Movie</option>
          <option value="tv">TV</option>
        </select>
        <input value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder={matchType === "id" ? "157336" : "Stargate"} required />
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why keep it?" />
        <button className="primary" type="submit">Protect</button>
      </form>
      <div className="list">
        {items.map((item) => (
          <div className="list-item" key={item.id}>
            <div>
              <strong>{item.pattern}</strong>
              <div className="muted">{item.match_type} · {item.media_type} {item.note && `· ${item.note}`}</div>
            </div>
            <button className="ghost" onClick={async () => { await api.removeWhitelist(item.id); await load(); }}>Remove</button>
          </div>
        ))}
        {!items.length && <p className="muted">Nothing protected yet.</p>}
      </div>
    </div>
  );
}

function Settings() {
  const [values, setValues] = useState<Record<string, string>>({});
  const [flags, setFlags] = useState<Record<string, boolean>>({});
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [usernameLocked, setUsernameLocked] = useState(false);
  const [hideSettings, setHideSettings] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [autoDelete, setAutoDelete] = useState(false);
  const [autoDeleteCap, setAutoDeleteCap] = useState("10");
  const [autoDeleteDays, setAutoDeleteDays] = useState("365");
  const [interval, setIntervalHours] = useState("24");
  const [tests, setTests] = useState<Record<string, ServiceTest | { status: string }>>({});
  const [testing, setTesting] = useState(false);
  const [maintenance, setMaintenance] = useState({ cache_files: 0, cache_bytes: 0, library_count: 0, people_count: 0, unmatched_count: 0 });
  const [busy, setBusy] = useState("");
  const services = [
    { id: "tautulli", label: "Tautulli", urlKey: "tautulli_url" },
    { id: "tracearr", label: "Tracearr", urlKey: "tracearr_url" },
    { id: "jellystat", label: "Jellystat", urlKey: "jellystat_url" },
    { id: "seerr", label: "Seerr", urlKey: "seerr_url" },
    { id: "radarr", label: "Radarr", urlKey: "radarr_url" },
    { id: "sonarr", label: "Sonarr", urlKey: "sonarr_url" },
  ] as const;
  const groups = [
    {
      title: "Watch history",
      copy: "Tautulli covers Plex. Tracearr covers Jellyfin, Plex, or Emby. Jellystat covers Jellyfin. Enable any mix; Cleanarr dedupes overlapping plays.",
      fields: [
        ["tautulli_url", "Tautulli URL"],
        ["tautulli_api_key", "Tautulli API key"],
        ["tracearr_url", "Tracearr URL"],
        ["tracearr_api_key", "Tracearr API key"],
        ["jellystat_url", "Jellystat URL"],
        ["jellystat_api_key", "Jellystat API key"],
      ],
    },
    {
      title: "Library",
      copy: "Used to list titles, sizes, ratings, and delete files from disk.",
      fields: [
        ["radarr_url", "Radarr URL"],
        ["radarr_api_key", "Radarr API key"],
        ["sonarr_url", "Sonarr URL"],
        ["sonarr_api_key", "Sonarr API key"],
      ],
    },
    {
      title: "Requests",
      copy: "Seerr / Jellyseerr / Overseerr supplies who requested a title and handles blacklist.",
      fields: [
        ["seerr_url", "Seerr URL"],
        ["seerr_api_key", "Seerr API key"],
      ],
    },
    {
      title: "Public links",
      copy: "Optional. If the apps are reached by a different hostname in the browser, set those here.",
      fields: [
        ["seerr_external_url", "Seerr public URL"],
        ["radarr_external_url", "Radarr public URL"],
        ["sonarr_external_url", "Sonarr public URL"],
        ["tautulli_external_url", "Tautulli public URL"],
        ["tracearr_external_url", "Tracearr public URL"],
        ["jellystat_external_url", "Jellystat public URL"],
      ],
    },
  ] as const;

  async function loadSettings() {
    const data = await api.settings();
    const next: Record<string, string> = {};
    const nextFlags: Record<string, boolean> = {};
    for (const [key, value] of Object.entries(data.values)) {
      if (typeof value === "boolean") nextFlags[key] = value;
      else next[key] = value;
    }
    setValues(next);
    setFlags(nextFlags);
    setUsername(data.username);
    setUsernameLocked(data.username_locked);
    setHideSettings(Boolean(data.hide_settings));
    setScheduleEnabled((next.sync_schedule_enabled || "0") === "1");
    setIntervalHours(next.sync_interval_hours || "24");
    setAutoDelete((next.auto_delete_enabled || "0") === "1");
    setAutoDeleteCap(next.auto_delete_max_per_run || "10");
    setAutoDeleteDays(next.auto_delete_stale_days || "365");
    if (data.maintenance) setMaintenance(data.maintenance);
  }

  useEffect(() => {
    loadSettings().catch((err) => setError(err instanceof Error ? err.message : "Could not load settings"));
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    setError("");
    const outgoing: Record<string, string> = {
      sync_schedule_enabled: scheduleEnabled ? "1" : "0",
      sync_interval_hours: interval,
      auto_delete_enabled: autoDelete ? "1" : "0",
      auto_delete_max_per_run: autoDeleteCap,
      auto_delete_stale_days: autoDeleteDays,
    };
    if (!hideSettings) {
      for (const [key, value] of Object.entries(values)) {
        if (APP_SETTING_KEYS.includes(key)) continue;
        if (flags[`${key}_hidden`] || flags[`${key}_locked`]) continue;
        if (key.endsWith("_api_key") && !value) continue;
        outgoing[key] = value;
      }
    }
    await api.saveSettings({
      values: outgoing,
      username: hideSettings || usernameLocked ? null : username,
      password: hideSettings || usernameLocked ? null : password || null,
    });
    setPassword("");
    setMessage(hideSettings ? "Schedule saved." : "Saved.");
  }

  function applyTest(result: ServiceTest) {
    setTests((current) => ({ ...current, [result.service]: result }));
  }

  async function test(service: string) {
    setTests((current) => ({ ...current, [service]: { status: "running" } }));
    try {
      applyTest(await api.test(service));
    } catch (err) {
      applyTest({
        service,
        ok: false,
        configured: true,
        message: err instanceof Error ? err.message : "failed",
      });
    }
  }

  async function testAll() {
    setTesting(true);
    setError("");
    for (const service of visibleServices) {
      setTests((current) => ({ ...current, [service.id]: { status: "running" } }));
    }
    try {
      const data = await api.testAll();
      for (const result of data.results) applyTest(result);
      const ok = data.results.filter((row) => row.ok).length;
      setMessage(`Tested ${ok}/${data.results.length} services.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Tests failed");
    } finally {
      setTesting(false);
    }
  }

  async function clearCache() {
    if (!window.confirm("Clear cached posters? They will be re-downloaded on the next sync or when a poster is viewed.")) return;
    setBusy("cache");
    setError("");
    try {
      const result = await api.clearCache();
      setMaintenance((current) => ({ ...current, cache_files: result.cache_files, cache_bytes: result.cache_bytes }));
      setMessage(`Cleared ${result.removed} cached posters.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not clear cache");
    } finally {
      setBusy("");
    }
  }

  async function clearLibrary() {
    if (!window.confirm("Clear the synced library, users, unmatched titles, and poster cache? Whitelist, login, and settings are kept. Run Sync now afterwards.")) return;
    setBusy("library");
    setError("");
    try {
      const result = await api.clearLibrary();
      await loadSettings();
      setMessage(`Cleared ${result.media} titles, ${result.people} users, and ${result.posters} posters.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not clear library");
    } finally {
      setBusy("");
    }
  }

  function hiddenKey(key: string) {
    return hideSettings || Boolean(flags[`${key}_hidden`]);
  }

  const visibleServices = hideSettings ? services.filter((service) => flags[`${service.urlKey}_set`]) : services;
  const visibleGroups = hideSettings
    ? []
    : groups
        .map((group) => ({ ...group, fields: group.fields.filter(([key]) => !hiddenKey(key)) }))
        .filter((group) => group.fields.length > 0);

  function field(key: string, label: string) {
    const locked = Boolean(flags[`${key}_locked`]);
    const configured = Boolean(flags[`${key}_set`]);
    const secret = key.includes("api_key");
    return (
      <label key={key}>
        {label} {locked && <span className="lock">env</span>}
        <input
          type={secret ? "password" : "url"}
          name={key}
          value={secret ? (locked ? "" : values[key] || "") : values[key] || ""}
          disabled={locked}
          autoComplete="off"
          placeholder={secret ? (configured ? "Configured — leave blank" : "API key") : "https://"}
          onChange={(e) => setValues((current) => ({ ...current, [key]: e.target.value }))}
        />
      </label>
    );
  }

  function testLabel(service: string, configured: boolean) {
    const result = tests[service];
    if (!result) return configured ? "Not tested" : "Not configured";
    if ("status" in result && result.status === "running") return "Testing…";
    if ("ok" in result) return result.message;
    return configured ? "Not tested" : "Not configured";
  }

  function testClass(service: string, configured: boolean) {
    const result = tests[service];
    if (!configured && !result) return "skip";
    if (result && "status" in result && result.status === "running") return "running";
    if (result && "ok" in result) return result.ok ? "ok" : "fail";
    return "skip";
  }

  return (
    <div className="page">
      <h2>Settings</h2>
      {hideSettings ? (
        <p className="muted">
          Service URLs, API keys, and login are hidden because <code>CLEANARR_HIDE_SETTINGS=1</code> is set. Edit <code>.env</code> and restart to change them.
        </p>
      ) : (
        <p className="muted">Values present in the process environment are locked. API keys are never shown after they are saved.</p>
      )}
      {error && <p className="error">{error}</p>}
      {message && <p className="muted">{message}</p>}

      {visibleServices.length > 0 && (
        <section className="settings-section">
          <div className="settings-head">
            <div>
              <h3>Connections</h3>
              <p className="muted">Probe each service without exposing API keys.</p>
            </div>
            <button className="primary" type="button" disabled={testing} onClick={testAll}>{testing ? "Testing…" : "Test all"}</button>
          </div>
          <div className="test-list">
            {visibleServices.map((service) => {
              const configured = Boolean(flags[`${service.urlKey}_set`]);
              return (
                <div className="test-row" key={service.id}>
                  <div>
                    <strong>{service.label}</strong>
                    <div className="muted">{configured ? "Configured" : "Not configured"}</div>
                  </div>
                  <span className={`test-status ${testClass(service.id, configured)}`}>{testLabel(service.id, configured)}</span>
                  <button className="ghost" type="button" disabled={testing || !configured} onClick={() => test(service.id)}>Test</button>
                </div>
              );
            })}
          </div>
        </section>
      )}

      <form onSubmit={save}>
        <section className="settings-section">
          <h3>Automatic sync</h3>
          <p className="muted">When enabled, Cleanarr syncs the library on this interval while the container is running.</p>
          <div className="schedule-row">
            <label className="toggle">
              <input type="checkbox" checked={scheduleEnabled} onChange={(e) => setScheduleEnabled(e.target.checked)} />
              <span className="toggle-track" />
              <span>{scheduleEnabled ? "Enabled" : "Disabled"}</span>
            </label>
            <label className="interval-field">
              Interval
              <select value={interval} onChange={(e) => setIntervalHours(e.target.value)} disabled={!scheduleEnabled}>
                <option value="1">Every hour</option>
                <option value="3">Every 3 hours</option>
                <option value="6">Every 6 hours</option>
                <option value="12">Every 12 hours</option>
                <option value="24">Every day</option>
                <option value="48">Every 2 days</option>
                <option value="168">Every week</option>
              </select>
            </label>
            <button className="primary" type="submit">Save schedule</button>
          </div>

          <h4>Automatic delete</h4>
          <p className="muted">
            Off by default. When on, each scheduled sync deletes titles Radarr/Sonarr added more than the
            cutoff ago that nobody has watched since — the Library's Stale / unwatched filter at the same
            cutoff. Files included. Whitelisted titles are always skipped, nothing is banned in Seerr, and
            a manual "Sync now" never deletes.
          </p>
          <div className="schedule-row">
            <label className="toggle">
              <input
                type="checkbox"
                checked={autoDelete}
                onChange={(e) => setAutoDelete(e.target.checked)}
                disabled={!scheduleEnabled}
              />
              <span className="toggle-track" />
              <span>{autoDelete && scheduleEnabled ? "Deleting" : "Alert only"}</span>
            </label>
            <label className="interval-field">
              Unwatched for
              <select
                value={autoDeleteDays}
                onChange={(e) => setAutoDeleteDays(e.target.value)}
                disabled={!autoDelete || !scheduleEnabled}
              >
                <option value="90">90 days</option>
                <option value="180">6 months</option>
                <option value="365">1 year</option>
                <option value="730">2 years</option>
              </select>
            </label>
            <label className="interval-field">
              Delete at most
              <select
                value={autoDeleteCap}
                onChange={(e) => setAutoDeleteCap(e.target.value)}
                disabled={!autoDelete || !scheduleEnabled}
              >
                <option value="5">5 per run</option>
                <option value="10">10 per run</option>
                <option value="25">25 per run</option>
                <option value="50">50 per run</option>
              </select>
            </label>
          </div>
          {autoDelete && scheduleEnabled && (
            <p className="muted">
              Titles are deleted from disk with no undo. A run is skipped if the watch history cannot be
              trusted for it — no source configured, a source that failed during the sync, or no plays
              reported at all — so an outage cannot make the library look unwatched.
            </p>
          )}
        </section>

        <section className="settings-section">
          <h3>Maintenance</h3>
          <p className="muted">Clearing the library removes synced titles, users, and unmatched rows. Whitelist, login, and connection settings stay.</p>
          <div className="maintenance-grid">
            <div>
              <span className="muted">Synced titles</span>
              <b>{maintenance.library_count}</b>
            </div>
            <div>
              <span className="muted">Users</span>
              <b>{maintenance.people_count}</b>
            </div>
            <div>
              <span className="muted">Unmatched</span>
              <b>{maintenance.unmatched_count}</b>
            </div>
            <div>
              <span className="muted">Poster cache</span>
              <b>{maintenance.cache_files} · {bytes(maintenance.cache_bytes)}</b>
            </div>
          </div>
          <div className="settings-actions">
            <button className="ghost" type="button" disabled={Boolean(busy)} onClick={clearCache}>
              {busy === "cache" ? "Clearing…" : "Clear poster cache"}
            </button>
            <button className="danger-ghost" type="button" disabled={Boolean(busy)} onClick={clearLibrary}>
              {busy === "library" ? "Clearing…" : "Clear synced library"}
            </button>
          </div>
        </section>

        {visibleGroups.map((group) => (
          <section className="settings-section" key={group.title}>
            <h3>{group.title}</h3>
            <p className="muted">{group.copy}</p>
            <div className="form-grid">{group.fields.map(([key, label]) => field(key, label))}</div>
          </section>
        ))}
        {!hideSettings && (
        <section className="settings-section">
          <h3>Account</h3>
          <div className="form-grid">
            <label>
              Cleanarr username {usernameLocked && <span className="lock">env</span>}
              <input value={username} disabled={usernameLocked} autoComplete="off" onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label>
              New password
              <input type="password" value={password} disabled={usernameLocked} autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} placeholder="Leave blank to keep" />
            </label>
          </div>
        </section>
        )}
        <div className="settings-actions">
          <button className="primary" type="submit">{hideSettings ? "Save schedule" : "Save settings"}</button>
        </div>
      </form>
    </div>
  );
}
