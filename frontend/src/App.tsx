import { FormEvent, MouseEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, IgnoredItem, LogItem, MediaItem, Person, ServiceTest, SyncStatus, UnmatchedItem, WhitelistItem } from "./api";
import { Brand } from "./Logo";

const PAGES = ["library", "unmatched", "users", "whitelist", "logs", "settings"] as const;

type Page = (typeof PAGES)[number];

function isPage(value: string): value is Page {
  return (PAGES as readonly string[]).includes(value);
}

// The backend serves index.html for any unknown path, so /users survives a refresh.
function pageFromLocation(): Page {
  const slug = window.location.pathname.replace(/^\/+|\/+$/g, "");
  return isPage(slug) ? slug : "library";
}

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
  requester: string;
  hideUnprocessed: boolean;
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
  requester: "",
  hideUnprocessed: true,
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

function scrollResultsTop() {
  const page = document.querySelector(".shell > .page");
  if (page instanceof HTMLElement) page.scrollTo(0, 0);
  else window.scrollTo(0, 0);
}

function num(value: number | null | undefined) {
  return (value ?? 0).toLocaleString();
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

function whenSync(ts: number | null | undefined) {
  if (!ts) return "";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return "Just now";
  if (diff < 3600) {
    const mins = Math.max(1, Math.floor(diff / 60));
    return `${mins}m ago`;
  }
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function SyncMeter({ sync }: { sync: SyncStatus }) {
  const running = sync.status === "running";
  const failed = sync.status === "error";
  const finishedAt = sync.finished_at ?? null;
  const ago = whenSync(finishedAt);
  const exact = finishedAt ? new Date(finishedAt * 1000).toLocaleString() : "";

  if (running) {
    const label = sync.step || sync.message || "Syncing…";
    const tip = [sync.message, sync.total ? `${sync.current || 0} / ${sync.total}` : ""]
      .filter(Boolean)
      .join(" · ");
    return (
      <div className="sync-meter live" title={tip || label}>
        <div className="sync-meter-row">
          <span className="spinner" aria-hidden="true" />
          <div className="sync-meter-text">
            <span className="sync-meter-label">{label}</span>
            {sync.percent != null ? <span className="sync-meter-meta">{sync.percent}%</span> : null}
          </div>
        </div>
        {sync.percent != null ? (
          <div className="sync-meter-bar" aria-hidden="true">
            <i style={{ width: `${Math.max(4, sync.percent)}%` }} />
          </div>
        ) : null}
      </div>
    );
  }

  if (failed) {
    return (
      <div className="sync-meter fail" title={sync.message || "Sync failed"}>
        <div className="sync-meter-text">
          <span className="sync-meter-label">Sync failed</span>
          <span className="sync-meter-meta">{ago || "See logs"}</span>
        </div>
      </div>
    );
  }

  if (!finishedAt && !sync.message) {
    return (
      <div className="sync-meter">
        <div className="sync-meter-text">
          <span className="sync-meter-label">Not synced yet</span>
          <span className="sync-meter-meta">Run Sync now</span>
        </div>
      </div>
    );
  }

  const tip = [sync.message, exact].filter(Boolean).join(" · ");
  return (
    <div className="sync-meter" title={tip || "Last sync"}>
      <div className="sync-meter-text">
        <span className="sync-meter-label">{sync.message || "Synced"}</span>
        <span className="sync-meter-meta">{ago ? `Synced ${ago}` : "Synced"}</span>
      </div>
    </div>
  );
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

// Seerr's own view of a title, so the row says why it is listed instead of just "unmatched".
const SEERR_STATE_CHIPS: Record<string, { label: string; tone: string }> = {
  available: { label: "Seerr says available", tone: "warn" },
  orphan: { label: "Dead Radarr/Sonarr link", tone: "warn" },
  requested: { label: "Never arrived", tone: "partial" },
  deleted: { label: "Deleted in Seerr", tone: "pending" },
  absent: { label: "Not in Seerr", tone: "partial" },
};

function stateChip(item: UnmatchedItem) {
  const known = item.seerr_state ? SEERR_STATE_CHIPS[item.seerr_state] : undefined;
  if (known) return known;
  return item.kind === "no_seerr"
    ? SEERR_STATE_CHIPS.absent
    : item.kind === "seerr_deleted"
      ? SEERR_STATE_CHIPS.deleted
      : { label: "Not in library", tone: "warn" };
}

function isInteractive(event: MouseEvent) {
  return Boolean((event.target as HTMLElement).closest("a, button, input, label"));
}

const SERVICE_META: Record<string, { label: string; short: string; className: string }> = {
  radarr: { label: "Radarr", short: "Rad", className: "radarr" },
  sonarr: { label: "Sonarr", short: "Son", className: "sonarr" },
  seerr: { label: "Seerr", short: "See", className: "seerr" },
  tautulli: { label: "Tautulli", short: "Tau", className: "tautulli" },
  tracearr: { label: "Tracearr", short: "Tra", className: "tracearr" },
  jellystat: { label: "Jellystat", short: "Jel", className: "jellystat" },
};

function ServiceLinks({ links }: { links?: Record<string, string> }) {
  const entries = Object.entries(SERVICE_META).filter(([key]) => links?.[key]);
  if (!entries.length) return <span className="muted">—</span>;
  return (
    <div className="service-links" role="list">
      {entries.map(([key, meta]) => (
        <a
          key={key}
          role="listitem"
          className={`service-pill ${meta.className}`}
          href={links?.[key]}
          target="_blank"
          rel="noreferrer"
          title={`Open in ${meta.label}`}
          aria-label={`Open in ${meta.label}`}
        >
          <span className="service-pill-short">{meta.short}</span>
          <span className="service-pill-full">{meta.label}</span>
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
  const [page, setPage] = useState<Page>(pageFromLocation);
  const [sync, setSync] = useState<SyncStatus>({ status: "idle", message: "" });
  const [unmatchedCount, setUnmatchedCount] = useState(0);
  const prevSync = useRef(sync.status);

  // replace: for redirects, so Back does not bounce straight back to the page we left.
  const go = useCallback((next: Page, replace = false) => {
    setPage(next);
    if (pageFromLocation() === next) return;
    const url = `/${next}`;
    if (replace) window.history.replaceState(null, "", url);
    else window.history.pushState(null, "", url);
  }, []);

  useEffect(() => {
    const slug = window.location.pathname.replace(/^\/+|\/+$/g, "");
    if (slug && !isPage(slug)) window.history.replaceState(null, "", "/library");
    const onPop = () => setPage(pageFromLocation());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  function refreshUnmatched() {
    api.unmatched({ page_size: "1" }).then((data) => {
      setUnmatchedCount(data.stats.actionable ?? data.stats.count ?? 0);
    }).catch(() => undefined);
  }

  useEffect(() => {
    api.syncStatus().then(setSync).catch(() => undefined);
    refreshUnmatched();
  }, []);

  useEffect(() => {
    if (!unmatchedCount && page === "unmatched") go("library", true);
  }, [unmatchedCount, page, go]);

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
        <div className="topbar-main">
          <Brand compact />
          <nav className="nav" aria-label="Primary">
            <button className={page === "library" ? "active" : ""} onClick={() => go("library")}>Library</button>
            {unmatchedCount > 0 && (
              <button className={`alert ${page === "unmatched" ? "active" : ""}`} onClick={() => go("unmatched")}>
                Unmatched<span className="nav-count">{unmatchedCount}</span>
              </button>
            )}
            <button className={page === "users" ? "active" : ""} onClick={() => go("users")}>Users</button>
            <button className={page === "whitelist" ? "active" : ""} onClick={() => go("whitelist")}>Whitelist</button>
            <button className={page === "logs" ? "active" : ""} onClick={() => go("logs")}>Logs</button>
            <button className={page === "settings" ? "active" : ""} onClick={() => go("settings")}>Settings</button>
          </nav>
        </div>
        <div className="topbar-aside">
          <div className="topbar-sync">
            <SyncMeter sync={sync} />
            <button
              className="primary"
              disabled={sync.status === "running"}
              onClick={async () => { setSync(await api.sync()); }}
            >
              {sync.status === "running" ? "Syncing…" : "Sync now"}
            </button>
          </div>
          <div className="topbar-account">
            <span className="muted account-name">{user}</span>
            <button className="ghost" onClick={async () => { await api.logout(); onLogout(); }}>Sign out</button>
          </div>
        </div>
      </header>
      {page === "library" && <Library sync={sync} setSync={setSync} unmatchedCount={unmatchedCount} onOpenUnmatched={() => go("unmatched")} onUnmatchedCount={setUnmatchedCount} />}
      {page === "unmatched" && unmatchedCount > 0 && (
        <Unmatched sync={sync} setSync={setSync} onUnmatchedCount={(count) => {
          setUnmatchedCount(count);
          if (!count) go("library", true);
        }} />
      )}
      {page === "users" && <Users
        onOpenLibrary={(q) => {
          sessionStorage.setItem(FILTERS_KEY, JSON.stringify({ ...defaultFilters, q }));
          go("library");
        }}
        onOpenRequests={(name) => {
          sessionStorage.setItem(FILTERS_KEY, JSON.stringify({
            ...defaultFilters,
            requester: name,
            sort: "requests",
            hideUnprocessed: true,
          }));
          go("library");
        }}
      />}
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
        requester: next.requester,
        hide_unprocessed: next.hideUnprocessed ? "true" : "false",
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
  }, [filters.q, filters.mediaType, filters.watched, filters.sort, filters.page, filters.pageSize, filters.staleDays, filters.maxRating, filters.requester, filters.hideUnprocessed]);

  useEffect(() => {
    setSelected(new Set());
  }, [filters.q, filters.mediaType, filters.watched, filters.sort, filters.page, filters.pageSize, filters.staleDays, filters.maxRating, filters.requester, filters.hideUnprocessed]);

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
      <div className="page-head">
        <div>
          <h2>Library</h2>
          <p className="page-intro muted">
            Everything Radarr and Sonarr hold, with watch history and who requested it. Pick titles to delete, or whitelist the ones to keep.
          </p>
        </div>
      </div>
      <div className="stats">
        <button className={`stat ${!filters.watched && !filters.maxRating ? "active" : ""}`} onClick={() => patch({ watched: "", maxRating: "" })}>
          <span className="muted">Matching titles</span><b>{num(stats.count)}</b>
        </button>
        <button className={`stat never ${filters.watched === "never" ? "active" : ""}`} onClick={() => patch(filters.watched === "never" ? { watched: "" } : { watched: "never", sort: "size" })}>
          <span className="muted">Never watched</span><b>{num(stats.never_watched)}</b>
        </button>
        <button className={`stat stale ${filters.watched === "stale" ? "active" : ""}`} onClick={() => patch(filters.watched === "stale" ? { watched: "" } : { watched: "stale", sort: "oldest" })}>
          <span className="muted">Stale / unwatched</span><b>{num(stats.stale)}</b>
        </button>
        <button className={`stat pending ${filters.watched === "requested" ? "active" : ""}`} onClick={() => patch(filters.watched === "requested" ? { watched: "" } : { watched: "requested", maxRating: "", sort: "title" })}>
          <span className="muted">Requested</span><b>{num(stats.requested)}</b>
        </button>
        <button className={`stat ok ${filters.watched === "protected" ? "active" : ""}`} onClick={() => patch(filters.watched === "protected" ? { watched: "" } : { watched: "protected", maxRating: "", sort: "title" })}>
          <span className="muted">Protected</span><b>{num(stats.whitelisted)}</b>
        </button>
        {unmatchedCount > 0 && (
          <button className="stat warn" onClick={onOpenUnmatched}>
            <span className="muted">Unmatched</span><b>{num(unmatchedCount)}</b>
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
        <button
          className={`chip-btn ${filters.hideUnprocessed ? "active" : ""}`}
          onClick={() => patch({ hideUnprocessed: !filters.hideUnprocessed })}
        >
          Hide unprocessed
        </button>
        {filters.requester ? (
          <button className="chip-btn active" onClick={() => patch({ requester: "", sort: "oldest" })}>
            Requester: {filters.requester} ×
          </button>
        ) : null}
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
          <option value="requests">Never watched, then oldest request</option>
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
              <th className="col-rating">Rating</th>
              <th className="col-watched">Last watched</th>
              <th className="col-plays">Plays</th>
              <th className="col-watchers">Watchers</th>
              <th className="col-requested">Requested by</th>
              <th className="col-size">Size</th>
              <th className="col-links">Links</th>
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
                <td className="tick-cell" data-label="">
                  <input className="tick" type="checkbox" disabled={item.whitelisted} checked={selected.has(item.id)} onChange={() => toggle(item.id)} />
                </td>
                <td data-label="Title">
                  <div className="title-cell">
                    {item.art_url ? <img className="poster" src={item.art_url} alt="" /> : <div className="poster placeholder">No art</div>}
                    <div>
                      <strong>{item.title}</strong> {item.year ? <span className="muted">({item.year})</span> : null}
                      <div className="title-meta">
                        <span className={`type-chip ${item.media_type}`}>{item.media_type === "movie" ? "Movie" : "TV"}</span>
                        {availabilityLabel(item.availability) ? <span className={`chip ${item.availability === "requested" ? "pending" : "partial"}`}>{availabilityLabel(item.availability)}</span> : null}
                        {item.whitelisted
                          ? <span className="chip ok" title={item.whitelist_reason}>Protected · {item.whitelist_reason}</span>
                          : <button type="button" className="keep-btn" onClick={() => keep(item)}>Whitelist</button>}
                      </div>
                    </div>
                  </div>
                </td>
                <td className="rating col-rating" data-label="Rating" title={item.rating_source ? `${item.rating_source} · ${item.rating_votes} votes` : "No rating"}>
                  {item.rating != null ? <><strong>{Number(item.rating).toFixed(1)}</strong> <span className="muted">/10</span></> : "—"}
                </td>
                <td className="col-watched" data-label="Last watched" title={item.availability === "requested" ? "Requested, not downloaded yet" : whenFull(item.last_watched_at)}>
                  {item.availability === "requested" ? "—" : when(item.last_watched_at)}
                </td>
                <td className="col-plays" data-label="Plays">{item.play_count}</td>
                <td className="watchers col-watchers" data-label="Watchers" title={item.watchers.map((watcher) => `${watcher.user} ×${watcher.plays}`).join(", ")}>
                  {item.watchers.length
                    ? `${item.watchers.slice(0, 2).map((watcher) => `${watcher.user} ×${watcher.plays}`).join(", ")}${item.watchers.length > 2 ? ` +${item.watchers.length - 2}` : ""}`
                    : "—"}
                </td>
                <td className="col-requested" data-label="Requested by"><Requester name={item.requested_by} at={item.requested_at} /></td>
                <td className="col-size" data-label="Size">{bytes(item.size_bytes)}</td>
                <td className="col-links" data-label="Links">
                  <div className="row-actions">
                    <ServiceLinks links={item.links} />
                  </div>
                </td>
              </tr>
            ))}
            {!items.length && (
              <tr>
                <td colSpan={9} className="empty">
                  {loading ? (
                    <strong>Loading library…</strong>
                  ) : (
                    <>
                      <strong>Nothing to show here</strong>
                      <span>
                        {filters.watched === "protected"
                          ? "No library titles match the current whitelist."
                          : filters.watched === "requested"
                            ? "Nothing is sitting in a requested, not-downloaded state."
                            : "No titles match these filters. Try Never watched or Oldest / stale."}
                      </span>
                    </>
                  )}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <span className="muted">{num(stats.count)} titles · {bytes(stats.size_bytes || 0)}</span>
        <div className="spacer" />
        <select value={filters.pageSize} onChange={(e) => patch({ pageSize: e.target.value })}>
          <option value="25">25 / page</option>
          <option value="50">50 / page</option>
          <option value="100">100 / page</option>
        </select>
        <span className="muted">Page {stats.page || 1} of {pages}</span>
        <button className="ghost" disabled={(stats.page || 1) <= 1} onClick={() => { setFilters((current) => ({ ...current, page: current.page - 1 })); scrollResultsTop(); }}>Previous</button>
        <button className="ghost" disabled={(stats.page || 1) >= pages} onClick={() => { setFilters((current) => ({ ...current, page: current.page + 1 })); scrollResultsTop(); }}>Next</button>
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
            <div className="modal-actions">
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
  const [pending, setPending] = useState<null | { mode: "clear" | "add"; all: boolean; ids: number[] }>(null);
  const [ignored, setIgnored] = useState<IgnoredItem[]>([]);
  const [showIgnored, setShowIgnored] = useState(false);
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
      onUnmatchedCount(data.stats.actionable ?? data.stats.count ?? 0);
      if (data.stats.ignored) {
        setIgnored((await api.ignoredUnmatched()).items);
      } else {
        setIgnored([]);
      }
    } finally {
      setLoading(false);
    }
  }

  async function ignore(ids: number[]) {
    setBusy(true);
    setError("");
    try {
      const result = await api.ignoreUnmatched(ids);
      setSelected(new Set());
      onUnmatchedCount(result.remaining);
      await load(page);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not ignore those titles");
    } finally {
      setBusy(false);
    }
  }

  async function unignore(id: number) {
    setBusy(true);
    setError("");
    try {
      await api.unignoreUnmatched(id);
      setIgnored((current) => current.filter((item) => item.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not restore that title");
    } finally {
      setBusy(false);
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
  const missingItems = items.filter((item) => item.kind === "no_seerr" && item.tmdb_id > 0);
  const missingCount = stats.no_seerr ?? 0;
  const actionable = items.filter((item) => item.kind === "seerr_missing" || (item.kind === "no_seerr" && item.tmdb_id > 0));
  const selectedStale = staleItems.filter((item) => selected.has(item.id)).map((item) => item.id);
  const selectedMissing = missingItems.filter((item) => selected.has(item.id)).map((item) => item.id);

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
      const result =
        pending.mode === "add"
          ? await api.addSeerr(pending.all ? { all_missing: true } : { ids: pending.ids })
          : await api.clearSeerr(pending.all ? { all_stale: true } : { ids: pending.ids });
      const failed = result.results.filter((row) => !row.ok);
      if (failed.length) setError(failed.map((row) => `${row.title}: ${row.error}`).join(" · "));
      setSelected(new Set());
      setPending(null);
      onUnmatchedCount(result.remaining);
      if (result.remaining) await load(page);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : pending.mode === "add"
            ? "Could not add these titles to Seerr"
            : "Could not clear Seerr records",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <h2>Unmatched</h2>
      <p className="page-intro muted">
        Gaps between Radarr/Sonarr and Seerr. Clear stale Seerr records so those titles can be requested again, or add
        library titles Seerr does not track yet. Titles Seerr has already deleted are listed separately: they need no
        action because anyone can request them again.
      </p>
      <div className="stats">
        <button className={`stat ${!kind ? "active" : ""}`} onClick={() => { setKind(""); setPage(1); }}>
          <span className="muted">All gaps</span><b>{num(stats.count ?? total)}</b>
        </button>
        <button className={`stat warn ${kind === "seerr_missing" ? "active" : ""}`} onClick={() => { setKind(kind === "seerr_missing" ? "" : "seerr_missing"); setPage(1); }}>
          <span className="muted">Stale in Seerr</span><b>{num(staleCount)}</b>
        </button>
        <button className={`stat ${kind === "no_seerr" ? "active" : ""}`} onClick={() => { setKind(kind === "no_seerr" ? "" : "no_seerr"); setPage(1); }}>
          <span className="muted">Not in Seerr</span><b>{num(stats.no_seerr)}</b>
        </button>
        <button className={`stat pending ${kind === "seerr_deleted" ? "active" : ""}`} onClick={() => { setKind(kind === "seerr_deleted" ? "" : "seerr_deleted"); setPage(1); }}>
          <span className="muted">Deleted in Seerr</span><b>{num(stats.seerr_deleted)}</b>
        </button>
      </div>
      <div className="filters">
        <input type="search" placeholder="Search unmatched titles" value={qInput} onChange={(e) => setQInput(e.target.value)} />
        <select value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }}>
          <option value="">All gaps</option>
          <option value="seerr_missing">Stale in Seerr</option>
          <option value="no_seerr">Library not in Seerr</option>
          <option value="seerr_deleted">Deleted in Seerr</option>
        </select>
        <select value={mediaType} onChange={(e) => { setMediaType(e.target.value); setPage(1); }}>
          <option value="">Movies & TV</option>
          <option value="movie">Movies</option>
          <option value="tv">TV</option>
        </select>
        <div className="spacer" />
        {missingCount > 0 && (
          <button className="ghost" type="button" onClick={() => setPending({ mode: "add", all: true, ids: [] })}>Add all to Seerr</button>
        )}
        {staleCount > 0 && (
          <button className="danger" type="button" onClick={() => setPending({ mode: "clear", all: true, ids: [] })}>Clear all stale Seerr</button>
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
                  checked={actionable.length > 0 && actionable.every((item) => selected.has(item.id))}
                  disabled={!actionable.length}
                  onChange={(e) => setSelected(e.target.checked ? new Set(actionable.map((item) => item.id)) : new Set())}
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
              const missing = item.kind === "no_seerr";
              const pickable = stale || (missing && item.tmdb_id > 0);
              return (
                <tr
                  key={item.id}
                  className={`${pickable ? "unmatched-row clickable" : "info-row"} ${selected.has(item.id) ? "selected" : ""}`}
                  onClick={(event) => {
                    if (!pickable || isInteractive(event)) return;
                    toggle(item.id);
                  }}
                >
                  <td className="tick-cell" data-label="">
                    <input className="tick" type="checkbox" disabled={!pickable} checked={selected.has(item.id)} onChange={() => toggle(item.id)} />
                  </td>
                  <td data-label="Title">
                    <strong>{item.title}</strong> {item.year ? <span className="muted">({item.year})</span> : null}
                    <div className="title-meta">
                      <span className={`type-chip ${item.media_type}`}>{item.media_type === "tv" ? "TV" : "Movie"}</span>
                      <span className={`chip ${stateChip(item).tone}`}>{stateChip(item).label}</span>
                    </div>
                  </td>
                  <td className="capitalize col-where" data-label="Where">{item.source}</td>
                  <td className="col-type" data-label="Type">{item.media_type === "tv" ? "TV" : "Movie"}</td>
                  <td className="col-requested" data-label="Requested by"><Requester name={item.requested_by} at={item.requested_at} /></td>
                  <td className="muted col-why" data-label="Why">{item.reason}</td>
                  <td className="col-links" data-label="Actions">
                    <div className="row-actions">
                      {stale && <button className="danger-ghost" type="button" onClick={() => setPending({ mode: "clear", all: false, ids: [item.id] })}>Clear in Seerr</button>}
                      {missing && (
                        <button
                          className="ghost"
                          type="button"
                          disabled={!item.tmdb_id}
                          title={item.tmdb_id ? undefined : "Seerr is keyed on TMDB and this title has no TMDB id"}
                          onClick={() => setPending({ mode: "add", all: false, ids: [item.id] })}
                        >
                          Add to Seerr
                        </button>
                      )}
                      <button className="ghost" type="button" disabled={busy} onClick={() => ignore([item.id])}>Ignore</button>
                      <ServiceLinks links={item.links} />
                    </div>
                  </td>
                </tr>
              );
            })}
            {!items.length && (
              <tr>
                <td colSpan={7} className="empty">
                  {loading ? (
                    <strong>Loading unmatched titles…</strong>
                  ) : (
                    <>
                      <strong>Nothing to reconcile</strong>
                      <span>Radarr, Sonarr, and Seerr agree on the current library.</span>
                    </>
                  )}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {ignored.length > 0 && (
        <div className="ignored">
          <button className="ghost" type="button" onClick={() => setShowIgnored((current) => !current)}>
            {showIgnored ? "Hide" : "Show"} {ignored.length} ignored
          </button>
          {showIgnored && (
            <ul className="ignored-list">
              {ignored.map((item) => (
                <li key={item.id}>
                  <span>{item.title}</span>
                  <span className="muted">{item.reason}</span>
                  <button className="ghost" type="button" disabled={busy} onClick={() => unignore(item.id)}>Stop ignoring</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="pager">
        <span className="muted">{num(total)} listed</span>
        <div className="spacer" />
        <span className="muted">Page {page} of {pages}</span>
        <button className="ghost" disabled={page <= 1} onClick={() => { setPage((current) => current - 1); scrollResultsTop(); }}>Previous</button>
        <button className="ghost" disabled={page >= pages} onClick={() => { setPage((current) => current + 1); scrollResultsTop(); }}>Next</button>
      </div>
      {selected.size > 0 && (
        <div className="bulk">
          <strong>{selected.size} selected</strong>
          <span className="muted">Clear stale Seerr media, or add library titles Seerr is missing</span>
          <div className="spacer" />
          {selectedMissing.length > 0 && (
            <button className="ghost" onClick={() => setPending({ mode: "add", all: false, ids: selectedMissing })}>
              Add {selectedMissing.length} to Seerr
            </button>
          )}
          <button className="ghost" disabled={busy} onClick={() => ignore([...selected])}>Ignore {selected.size}</button>
          {selectedStale.length > 0 && (
            <button className="danger" onClick={() => setPending({ mode: "clear", all: false, ids: selectedStale })}>
              Clear {selectedStale.length} in Seerr
            </button>
          )}
        </div>
      )}
      {pending && (
        <div className="modal-back" onClick={() => !busy && setPending(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>
              {pending.mode === "add"
                ? pending.all ? "Add all missing titles to Seerr" : "Add to Seerr"
                : pending.all ? "Clear all stale Seerr records" : "Clear in Seerr"}
            </h2>
            <p>
              {pending.mode === "add"
                ? "This requests each title in Seerr so it tracks what Radarr and Sonarr already hold. Nothing is downloaded again."
                : "This deletes the stale media records in Seerr so people can request them again. Radarr and Sonarr are not touched."}
            </p>
            <ul>
              {(pending.all
                ? pending.mode === "add" ? missingItems : staleItems
                : items.filter((item) => pending.ids.includes(item.id))
              ).slice(0, 8).map((item) => <li key={item.id}>{item.title}</li>)}
              {pending.all && pending.mode === "add" && missingCount > missingItems.length && (
                <li>…and {missingCount - missingItems.length} more</li>
              )}
              {pending.all && pending.mode === "clear" && staleCount > staleItems.length && (
                <li>…and {staleCount - staleItems.length} more</li>
              )}
              {!pending.all && pending.ids.length > 8 && <li>…and {pending.ids.length - 8} more</li>}
            </ul>
            <div className="modal-actions">
              <button className="ghost" disabled={busy} onClick={() => setPending(null)}>Cancel</button>
              <button className={pending.mode === "add" ? "primary" : "danger"} disabled={busy} onClick={confirm}>
                {busy ? (pending.mode === "add" ? "Adding…" : "Clearing…") : "Confirm"}
              </button>
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
      <p className="page-intro muted">Sync progress, matching, and deletions. Newest first.</p>
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
        <span className="muted">{num(total)} entries</span>
      </div>
      <div className="log-list">
        {items.map((item) => (
          <div className={`log-row ${item.level}`} key={item.id}>
            <span className={`log-level ${item.level}`}>{item.level}</span>
            <div>
              <div className="log-message">{item.message}</div>
              <div className="log-meta">
                <span title={new Date(item.created_at * 1000).toLocaleString()}>{when(item.created_at)}</span>
                {item.category ? <span className="log-tag">{item.category}</span> : null}
                {item.action ? <span className="log-tag">{item.action.replace(/[-_]/g, " ")}</span> : null}
                {item.actor ? <span>{item.actor}</span> : null}
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
        <button className="ghost" disabled={page <= 1} onClick={() => { load(page - 1); scrollResultsTop(); }}>Previous</button>
        <span className="muted">Page {page} of {pages}</span>
        <button className="ghost" disabled={page >= pages} onClick={() => { load(page + 1); scrollResultsTop(); }}>Next</button>
      </div>
    </div>
  );
}

function Users({
  onOpenLibrary,
  onOpenRequests,
}: {
  onOpenLibrary: (q: string) => void;
  onOpenRequests: (name: string) => void;
}) {
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
      <p className="page-intro muted">Seerr requesters and Tautulli/Tracearr/Jellystat watchers are matched across connected services when possible.</p>
      <div className="stats">
        <div className="stat static"><span className="muted">People</span><b>{num(stats.users)}</b></div>
        <div className="stat static"><span className="muted">Requests in library</span><b>{num(stats.requests)}</b></div>
        <div className="stat static"><span className="muted">Plays</span><b>{num(stats.plays)}</b></div>
        <button className={`stat warn ${onlyUnmatched ? "active" : ""}`} onClick={() => setOnlyUnmatched((current) => !current)}>
          <span className="muted">Unmatched</span><b>{num(stats.unmatched)}</b>
        </button>
      </div>
      <div className="filters">
        <input type="search" placeholder="Search name, username, email" value={qInput} onChange={(e) => setQInput(e.target.value)} />
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
              <th className="col-requests">Requests</th>
              <th className="col-library">In library</th>
              <th className="col-plays">Plays</th>
              <th className="col-size">Requested size</th>
              <th className="col-watched">Last watched</th>
              <th className="col-links">Links</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((person) => (
              <tr key={person.canonical} className={person.matched ? "" : "unmatched-row"}>
                <td data-label="User">
                  <strong>{person.display_name}</strong>
                  <div className="muted">
                    {[person.plex_username !== person.display_name ? person.plex_username : "", person.email].filter(Boolean).join(" · ")}
                  </div>
                  {!person.matched && <span className="chip warn">Unmatched</span>}
                </td>
                <td className="col-requests" data-label="Requests">{person.request_count}</td>
                <td className="col-library" data-label="In library">{person.library_count}</td>
                <td className="col-plays" data-label="Plays">{person.play_count}</td>
                <td className="col-size" data-label="Requested size">{bytes(person.library_size)}</td>
                <td className="col-watched" data-label="Last watched" title={whenFull(person.last_watched_at)}>{when(person.last_watched_at)}</td>
                <td className="col-links" data-label="Links">
                  <div className="row-actions">
                    <button className="ghost" onClick={() => onOpenRequests(person.display_name)}>Requests</button>
                    <button className="ghost" onClick={() => onOpenLibrary(person.display_name)}>Library</button>
                    <ServiceLinks links={person.links} />
                  </div>
                </td>
              </tr>
            ))}
            {!visible.length && (
              <tr>
                <td colSpan={7} className="empty">
                  <strong>{onlyUnmatched ? "Everyone is matched" : "No users yet"}</strong>
                  <span>
                    {onlyUnmatched
                      ? "Every listed person matched across connected services."
                      : "Run a sync to pull people from Seerr, Tautulli, Tracearr, and Jellystat."}
                  </span>
                </td>
              </tr>
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
      <p className="page-intro muted">Title matches are case-insensitive substrings. “Stargate” or “Back to the Future” protects the franchise. You can also whitelist a title straight from the library list.</p>
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
        message: "Failed",
        detail: err instanceof Error ? err.message : "failed",
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
      const total = data.results.length;
      if (!total) setMessage("No services are configured to test.");
      else if (ok === total) setMessage(`All ${total} configured service${total === 1 ? "" : "s"} passed.`);
      else setMessage(`${ok} of ${total} configured services passed.`);
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

  const visibleServices = services.filter((service) => flags[`${service.urlKey}_set`]);
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

  function testResult(service: string) {
    const result = tests[service];
    if (!result) {
      return { kind: "idle" as const, label: "Not tested yet", detail: "" };
    }
    if ("status" in result && result.status === "running") {
      return { kind: "running" as const, label: "Testing…", detail: "" };
    }
    if ("ok" in result) {
      if (result.ok) {
        return { kind: "ok" as const, label: result.message || "Passed", detail: result.detail || "" };
      }
      return {
        kind: "fail" as const,
        label: result.message || "Failed",
        detail: result.detail || "",
      };
    }
    return { kind: "idle" as const, label: "Not tested yet", detail: "" };
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
      {message && <p className="ok-message">{message}</p>}

      {visibleServices.length > 0 && (
        <section className="settings-section">
          <div className="settings-head">
            <div>
              <h3>Connections</h3>
              <p className="muted">
                {visibleServices.length} configured service{visibleServices.length === 1 ? "" : "s"}. Test that Cleanarr can reach each one.
              </p>
            </div>
            <button className="primary" type="button" disabled={testing} onClick={testAll}>{testing ? "Testing…" : "Test all"}</button>
          </div>
          <div className="test-list">
            {visibleServices.map((service) => {
              const result = testResult(service.id);
              return (
                <div className={`test-row ${result.kind}`} key={service.id}>
                  <div className="test-name">
                    <strong>{service.label}</strong>
                  </div>
                  <div className={`test-status ${result.kind}`}>
                    <span className={`test-badge ${result.kind}`}>
                      {result.kind === "ok" ? "Passed" : result.kind === "fail" ? "Failed" : result.kind === "running" ? "Testing" : "Idle"}
                    </span>
                    <span className="test-detail">
                      {result.kind === "ok"
                        ? (result.detail ? `Connected · ${result.detail}` : "Connected")
                        : result.kind === "fail"
                          ? (result.detail || result.label)
                          : result.label}
                    </span>
                  </div>
                  <button className="ghost" type="button" disabled={testing} onClick={() => test(service.id)}>Test</button>
                </div>
              );
            })}
          </div>
        </section>
      )}

      <form onSubmit={save}>
        <section className="settings-section">
          <div className="settings-head">
            <div>
              <h3>Automatic sync</h3>
              <p className="muted">Refresh the library on a timer while Cleanarr is running.</p>
            </div>
            <button className="primary" type="submit">Save</button>
          </div>
          <div className="settings-controls">
            <label className="toggle">
              <input type="checkbox" checked={scheduleEnabled} onChange={(e) => setScheduleEnabled(e.target.checked)} />
              <span className="toggle-track" />
              <span>{scheduleEnabled ? "On" : "Off"}</span>
            </label>
            <select
              className="control-select"
              value={interval}
              onChange={(e) => setIntervalHours(e.target.value)}
              disabled={!scheduleEnabled}
              aria-label="Sync interval"
            >
              <option value="1">Every hour</option>
              <option value="3">Every 3 hours</option>
              <option value="6">Every 6 hours</option>
              <option value="12">Every 12 hours</option>
              <option value="24">Every day</option>
              <option value="48">Every 2 days</option>
              <option value="168">Every week</option>
            </select>
          </div>

          <div className="settings-subsection">
            <div className="settings-head">
              <div>
                <h4>Automatic delete</h4>
                <p className="muted">
                  After each scheduled sync, remove stale titles from disk. Off by default. Whitelist always wins; Sync now never deletes.
                </p>
              </div>
            </div>
            <div className="settings-controls">
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={autoDelete}
                  onChange={(e) => setAutoDelete(e.target.checked)}
                  disabled={!scheduleEnabled}
                />
                <span className="toggle-track" />
                <span>{autoDelete && scheduleEnabled ? "On" : "Off"}</span>
              </label>
              <select
                className="control-select"
                value={autoDeleteDays}
                onChange={(e) => setAutoDeleteDays(e.target.value)}
                disabled={!autoDelete || !scheduleEnabled}
                aria-label="Unwatched cutoff"
              >
                <option value="90">Unwatched 90 days</option>
                <option value="180">Unwatched 6 months</option>
                <option value="365">Unwatched 1 year</option>
                <option value="730">Unwatched 2 years</option>
              </select>
              <select
                className="control-select"
                value={autoDeleteCap}
                onChange={(e) => setAutoDeleteCap(e.target.value)}
                disabled={!autoDelete || !scheduleEnabled}
                aria-label="Delete cap per run"
              >
                <option value="5">Up to 5 per run</option>
                <option value="10">Up to 10 per run</option>
                <option value="25">Up to 25 per run</option>
                <option value="50">Up to 50 per run</option>
              </select>
            </div>
            {autoDelete && scheduleEnabled && (
              <p className="settings-note">
                Skips a run if watch history looks untrustworthy (no source, a failed source, or zero plays).
              </p>
            )}
          </div>
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
        {!hideSettings && (
          <div className="settings-actions">
            <button className="primary" type="submit">Save settings</button>
          </div>
        )}
      </form>
    </div>
  );
}
