import { FormEvent, MouseEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, IgnoredItem, LogItem, MediaItem, Person, ServiceTest, SyncStatus, UnmatchedItem, WhitelistItem } from "./api";
import { Brand } from "./Logo";

const PAGES = ["library", "unmatched", "users", "whitelist", "logs", "settings"] as const;

type Page = (typeof PAGES)[number];

const NAV_ITEMS: { id: Page; label: string }[] = [
  { id: "library", label: "Library" },
  { id: "unmatched", label: "Unmatched" },
  { id: "users", label: "Users" },
  { id: "whitelist", label: "Whitelist" },
  { id: "logs", label: "Logs" },
  { id: "settings", label: "Settings" },
];

// The bottom bar on a phone is icon-first, so each tab needs a glyph as well as a word.
function NavIcon({ page }: { page: Page }) {
  const shared = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  if (page === "library") {
    return (
      <svg className="nav-icon" {...shared}>
        <rect x="3" y="4.5" width="18" height="15" rx="2.5" />
        <path d="M8 4.5v15M16 4.5v15M3 9.5h5M3 14.5h5M16 9.5h5M16 14.5h5" />
      </svg>
    );
  }
  if (page === "unmatched") {
    return (
      <svg className="nav-icon" {...shared}>
        <path d="M12 4.2 2.9 19.8h18.2z" />
        <path d="M12 10v4.2M12 17.2h.01" />
      </svg>
    );
  }
  if (page === "users") {
    return (
      <svg className="nav-icon" {...shared}>
        <path d="M15.5 19.5V18a3.5 3.5 0 0 0-3.5-3.5H7A3.5 3.5 0 0 0 3.5 18v1.5" />
        <circle cx="9.5" cy="7.5" r="3.3" />
        <path d="M20.5 19.5V18a3.5 3.5 0 0 0-2.6-3.4M15.4 4.4a3.3 3.3 0 0 1 0 6.2" />
      </svg>
    );
  }
  if (page === "whitelist") {
    return (
      <svg className="nav-icon" {...shared}>
        <path d="M12 3.2 19.2 6v5.5c0 4.3-2.9 7.5-7.2 9.3-4.3-1.8-7.2-5-7.2-9.3V6z" />
        <path d="m8.9 11.9 2.2 2.2 4-4.3" />
      </svg>
    );
  }
  if (page === "logs") {
    return (
      <svg className="nav-icon" {...shared}>
        <path d="M4 6.5h16M4 12h16M4 17.5h10" />
      </svg>
    );
  }
  return (
    <svg className="nav-icon" {...shared}>
      <path d="M4 7h7M15 7h5M4 17h5M13 17h7M4 12h13M21 12h-1" />
      <circle cx="13" cy="7" r="2" />
      <circle cx="11" cy="17" r="2" />
      <circle cx="19" cy="12" r="2" />
    </svg>
  );
}

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
  if (!value) return "–";
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
  if (!name) return <span className="muted">–</span>;
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
  radarr_4k: { label: "Radarr 4K", short: "4K", className: "radarr-4k" },
  sonarr: { label: "Sonarr", short: "Son", className: "sonarr" },
  seerr: { label: "Seerr", short: "See", className: "seerr" },
  tautulli: { label: "Tautulli", short: "Tau", className: "tautulli" },
  tracearr: { label: "Tracearr", short: "Tra", className: "tracearr" },
  jellystat: { label: "Jellystat", short: "Jel", className: "jellystat" },
};

function ServiceLinks({ links }: { links?: Record<string, string> }) {
  const entries = Object.entries(SERVICE_META).filter(([key]) => links?.[key]);
  if (!entries.length) return <span className="muted">–</span>;
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
  const [setupRequired, setSetupRequired] = useState(false);
  const [booting, setBooting] = useState(true);

  useEffect(() => {
    api.authStatus()
      .then(async (status) => {
        setSetupRequired(status.setup_required);
        if (status.setup_required) {
          setUser(null);
          return;
        }
        try {
          const me = await api.me();
          setUser(me.username);
        } catch {
          setUser(null);
        }
      })
      .catch(() => setUser(null))
      .finally(() => setBooting(false));
  }, []);

  if (booting) {
    return (
      <div className="login">
        <div className="login-card"><Brand /><p className="muted">Loading…</p></div>
      </div>
    );
  }
  if (setupRequired) {
    return (
      <Setup
        onDone={(name) => {
          setSetupRequired(false);
          setUser(name);
        }}
      />
    );
  }
  if (!user) return <Login onDone={setUser} />;
  return <Shell user={user} onLogout={() => setUser(null)} />;
}

function Setup({ onDone }: { onDone: (user: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (password !== confirm) {
      setError("Passwords do not match");
      return;
    }
    try {
      const result = await api.setup(username, password);
      onDone(result.username);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create account");
    }
  }

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <Brand />
        <h1>Create admin account</h1>
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
              required
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label>Password
            <input
              type="password"
              name="password"
              value={password}
              autoComplete="new-password"
              minLength={8}
              required
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <label>Confirm password
            <input
              type="password"
              name="confirm"
              value={confirm}
              autoComplete="new-password"
              minLength={8}
              required
              onChange={(e) => setConfirm(e.target.value)}
            />
          </label>
        </div>
        {error && <p className="error">{error}</p>}
        <button className="primary" type="submit">Create account</button>
      </form>
    </div>
  );
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

  const navItems = NAV_ITEMS.filter((item) => item.id !== "unmatched" || unmatchedCount > 0);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-main">
          <Brand compact />
          <nav className="nav nav-top" aria-label="Primary">
            {navItems.map((item) => (
              <button
                key={item.id}
                className={`${item.id === "unmatched" ? "alert " : ""}${page === item.id ? "active" : ""}`}
                aria-current={page === item.id ? "page" : undefined}
                onClick={() => go(item.id)}
              >
                {item.label}
                {item.id === "unmatched" ? <span className="nav-count">{unmatchedCount}</span> : null}
              </button>
            ))}
          </nav>
        </div>
        <div className="topbar-aside">
          <div className="topbar-sync">
            <SyncMeter sync={sync} />
            <button
              className="primary sync-button"
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
      {page === "whitelist" && (
        <Whitelist
          onOpenLibrary={(q) => {
            sessionStorage.setItem(FILTERS_KEY, JSON.stringify({ ...defaultFilters, q }));
            go("library");
          }}
        />
      )}
      {page === "logs" && <Logs sync={sync} />}
      {page === "settings" && <Settings />}
      <nav className="nav-bottom" aria-label="Primary">
        {navItems.map((item) => (
          <button
            key={item.id}
            className={`${item.id === "unmatched" ? "alert " : ""}${page === item.id ? "active" : ""}`}
            aria-current={page === item.id ? "page" : undefined}
            onClick={() => go(item.id)}
          >
            <span className="nav-bottom-icon">
              <NavIcon page={item.id} />
              {item.id === "unmatched" ? <span className="nav-bubble">{unmatchedCount > 99 ? "99+" : unmatchedCount}</span> : null}
            </span>
            <span className="nav-bottom-label">{item.label}</span>
          </button>
        ))}
      </nav>
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
      <div className="filters chips">
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
                    <div className="title-copy">
                      <strong>{item.title}</strong> {item.year ? <span className="muted">({item.year})</span> : null}
                      <div className="title-meta">
                        <span className={`type-chip ${item.media_type}`}>{item.media_type === "movie" ? "Movie" : "TV"}</span>
                        {availabilityLabel(item.availability) ? <span className={`chip ${item.availability === "requested" ? "pending" : "partial"}`}>{availabilityLabel(item.availability)}</span> : null}
                        {item.whitelisted
                          ? <span className="chip ok" title={item.whitelist_reason}>Protected · {item.whitelist_reason}</span>
                          : <button type="button" className="keep-btn" onClick={() => keep(item)}>Whitelist</button>}
                      </div>
                      <div className="card-stats" aria-hidden="true">
                        <span>{item.rating != null ? `${Number(item.rating).toFixed(1)}/10` : "No rating"}</span>
                        <span>{item.availability === "requested" ? "Not downloaded" : when(item.last_watched_at)}</span>
                        <span>{item.play_count} play{item.play_count === 1 ? "" : "s"}</span>
                        <span>{bytes(item.size_bytes)}</span>
                      </div>
                    </div>
                  </div>
                </td>
                <td className="rating col-rating" data-label="Rating" title={item.rating_source ? `${item.rating_source} · ${item.rating_votes} votes` : "No rating"}>
                  <span className="cell-value">{item.rating != null ? <><strong>{Number(item.rating).toFixed(1)}</strong> <span className="muted">/10</span></> : "–"}</span>
                </td>
                <td className="col-watched" data-label="Last watched" title={item.availability === "requested" ? "Requested, not downloaded yet" : whenFull(item.last_watched_at)}>
                  <span className="cell-value">{item.availability === "requested" ? "–" : when(item.last_watched_at)}</span>
                </td>
                <td className="col-plays" data-label="Plays"><span className="cell-value">{item.play_count}</span></td>
                <td className="watchers col-watchers" data-label="Watchers" title={item.watchers.map((watcher) => `${watcher.user} ×${watcher.plays}`).join(", ")}>
                  <span className="cell-value">
                    {item.watchers.length
                      ? `${item.watchers.slice(0, 2).map((watcher) => `${watcher.user} ×${watcher.plays}`).join(", ")}${item.watchers.length > 2 ? ` +${item.watchers.length - 2}` : ""}`
                      : "–"}
                  </span>
                </td>
                <td className="col-requested" data-label="Requested by"><span className="cell-value"><Requester name={item.requested_by} at={item.requested_at} /></span></td>
                <td className="col-size" data-label="Size"><span className="cell-value">{bytes(item.size_bytes)}</span></td>
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
                  <td className="capitalize col-where" data-label="Where"><span className="cell-value">{item.source}</span></td>
                  <td className="col-type" data-label="Type"><span className="cell-value">{item.media_type === "tv" ? "TV" : "Movie"}</span></td>
                  <td className="col-requested" data-label="Requested by"><span className="cell-value"><Requester name={item.requested_by} at={item.requested_at} /></span></td>
                  <td className="muted col-why" data-label="Why"><span className="cell-value">{item.reason}</span></td>
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

const BYTE_KEYS = new Set(["bytes", "freed_bytes", "cache_bytes", "size_bytes", "library_size"]);

function humanKey(key: string) {
  const words = key.replace(/[-_]/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function humanValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "–";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (BYTE_KEYS.has(key)) return bytes(value);
    if (key === "seconds") return value < 60 ? `${value}s` : `${Math.floor(value / 60)}m ${String(value % 60).padStart(2, "0")}s`;
    return num(value);
  }
  return String(value);
}

// Detail is free-form JSON from add_log, so render scalars as a fact grid and
// lists underneath rather than dumping raw JSON at the reader.
function LogDetail({ detail }: { detail: unknown }) {
  if (detail === null || detail === undefined || detail === "") return null;
  if (typeof detail === "string") return <p className="log-detail-text">{detail}</p>;
  if (Array.isArray(detail)) return <LogList items={detail} />;
  if (typeof detail !== "object") return <p className="log-detail-text">{String(detail)}</p>;

  const entries = Object.entries(detail as Record<string, unknown>);
  const facts = entries.filter(([, value]) => value === null || typeof value !== "object");
  const lists = entries.filter(([, value]) => Array.isArray(value) && (value as unknown[]).length > 0);
  if (!facts.length && !lists.length) return null;
  return (
    <div className="log-detail">
      {facts.length > 0 && (
        <dl className="log-facts">
          {facts.map(([key, value]) => (
            <div key={key}>
              <dt>{humanKey(key)}</dt>
              <dd>{humanValue(key, value)}</dd>
            </div>
          ))}
        </dl>
      )}
      {lists.map(([key, value]) => (
        <div className="log-sublist" key={key}>
          <span className="log-sublist-title">{humanKey(key)}</span>
          <LogList items={value as unknown[]} />
        </div>
      ))}
    </div>
  );
}

function LogList({ items }: { items: unknown[] }) {
  const shown = items.slice(0, 25);
  return (
    <ul className="log-titles">
      {shown.map((entry, index) => (
        <li key={index}>{describeEntry(entry)}</li>
      ))}
      {items.length > shown.length ? <li className="muted">…and {num(items.length - shown.length)} more</li> : null}
    </ul>
  );
}

function describeEntry(entry: unknown): string {
  if (entry === null || entry === undefined) return "–";
  if (typeof entry !== "object") return String(entry);
  const row = entry as Record<string, unknown>;
  const name = row.service ?? row.title ?? row.name ?? "";
  const outcome = row.ok === false ? row.error || row.detail || "failed" : row.message ?? row.detail ?? (row.ok === true ? "ok" : "");
  return [name, outcome].filter(Boolean).map(String).join(": ") || JSON.stringify(entry);
}

function dayHeading(ts: number) {
  const date = new Date(ts * 1000);
  const today = new Date();
  const yesterday = new Date(today.getTime() - 86400000);
  const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  if (sameDay(date, today)) return "Today";
  if (sameDay(date, yesterday)) return "Yesterday";
  return date.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: date.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}

function clockTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

const LOG_LEVEL_LABEL: Record<string, string> = { info: "Info", warn: "Warning", error: "Error" };

function Logs({ sync }: { sync: SyncStatus }) {
  const [items, setItems] = useState<LogItem[]>([]);
  const [category, setCategory] = useState("");
  const [level, setLevel] = useState("");
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [levels, setLevels] = useState({ info: 0, warn: 0, error: 0 });
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const timer = window.setTimeout(() => setQ(qInput), 250);
    return () => window.clearTimeout(timer);
  }, [qInput]);

  const load = useCallback(
    async (nextPage: number) => {
      const data = await api.logs({
        q,
        category,
        level,
        page: String(nextPage),
        page_size: "80",
      });
      setItems(data.items);
      setPages(data.pages || 1);
      setTotal(data.total || 0);
      setPage(data.page || nextPage);
      if (data.levels) setLevels(data.levels);
      setLoading(false);
    },
    [q, category, level],
  );

  useEffect(() => {
    setLoading(true);
    load(1).catch(() => setLoading(false));
  }, [load]);

  // Follow a running sync live; the log is the only place its steps are recorded.
  useEffect(() => {
    if (sync.status !== "running" || page !== 1) return;
    const timer = setInterval(() => load(1).catch(() => undefined), 1500);
    return () => clearInterval(timer);
  }, [sync.status, page, load]);

  function toggle(id: number) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function pickLevel(next: string) {
    setLevel((current) => (current === next ? "" : next));
    setPage(1);
  }

  let lastDay = "";

  return (
    <div className="page">
      <h2>Logs</h2>
      <p className="page-intro muted">
        Every sync step, match, and deletion, newest first. Open an entry to see the numbers behind it.
      </p>
      <div className="stats">
        <button className={`stat info ${!level ? "active" : ""}`} onClick={() => pickLevel("")}>
          <span className="muted">All entries</span><b>{num(levels.info + levels.warn + levels.error)}</b>
        </button>
        <button className={`stat stale ${level === "warn" ? "active" : ""}`} onClick={() => pickLevel("warn")}>
          <span className="muted">Warnings</span><b>{num(levels.warn)}</b>
        </button>
        <button className={`stat warn ${level === "error" ? "active" : ""}`} onClick={() => pickLevel("error")}>
          <span className="muted">Errors</span><b>{num(levels.error)}</b>
        </button>
      </div>
      <div className="filters">
        <input type="search" placeholder="Search logs" value={qInput} onChange={(e) => setQInput(e.target.value)} />
        <select value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }} aria-label="Category">
          <option value="">All categories</option>
          <option value="sync">Sync</option>
          <option value="match">Matching</option>
          <option value="audit">Audit</option>
          <option value="system">System</option>
        </select>
        <select value={level} onChange={(e) => { setLevel(e.target.value); setPage(1); }} aria-label="Level">
          <option value="">Any level</option>
          <option value="info">Info</option>
          <option value="warn">Warnings</option>
          <option value="error">Errors</option>
        </select>
        <div className="spacer" />
        <button className="ghost" type="button" onClick={() => load(page).catch(() => undefined)}>Refresh</button>
        <span className="muted">{num(total)} shown</span>
      </div>
      <div className="log-list">
        {items.map((item) => {
          const heading = dayHeading(item.created_at);
          const showHeading = heading !== lastDay;
          lastDay = heading;
          const hasDetail = Boolean(item.detail) && (typeof item.detail !== "object" || Object.keys(item.detail as object).length > 0);
          const expanded = open.has(item.id);
          return (
            <div key={item.id}>
              {showHeading && <div className="log-day">{heading}</div>}
              <div className={`log-row ${item.level || "info"} ${expanded ? "open" : ""}`}>
                <div className="log-accent" aria-hidden="true" />
                <div className="log-content">
                  <div className="log-topline">
                    <span className={`log-level ${item.level || "info"}`}>
                      {LOG_LEVEL_LABEL[item.level] || item.level || "Info"}
                    </span>
                    <time className="log-time" dateTime={new Date(item.created_at * 1000).toISOString()} title={new Date(item.created_at * 1000).toLocaleString()}>
                      {clockTime(item.created_at)}
                    </time>
                    <span className="log-ago">{when(item.created_at)}</span>
                    {hasDetail ? (
                      <button
                        className="log-toggle"
                        type="button"
                        aria-expanded={expanded}
                        aria-label={expanded ? "Hide details" : "Show details"}
                        onClick={() => toggle(item.id)}
                      >
                        {expanded ? "Less" : "Details"}
                      </button>
                    ) : null}
                  </div>
                  <div className="log-message">{item.message || "Untitled entry"}</div>
                  {(item.category || item.action || item.actor) ? (
                    <div className="log-meta">
                      {item.category ? <span className={`log-tag category-${item.category}`}>{item.category}</span> : null}
                      {item.action ? <span className="log-tag subtle">{item.action.replace(/[-_]/g, " ")}</span> : null}
                      {item.actor ? <span className="log-actor">by {item.actor}</span> : null}
                    </div>
                  ) : null}
                  {hasDetail && expanded ? <LogDetail detail={item.detail} /> : null}
                </div>
              </div>
            </div>
          );
        })}
        {!items.length && (
          <div className="empty log-empty">
            <strong>{loading ? "Loading logs…" : "Nothing logged yet"}</strong>
            {!loading && <span>{q || category || level ? "No entries match these filters." : "Run a sync and the steps will show up here."}</span>}
          </div>
        )}
      </div>
      <div className="pager">
        <span className="muted">Page {page} of {pages}</span>
        <div className="spacer" />
        <button className="ghost" disabled={page <= 1} onClick={() => { load(page - 1); scrollResultsTop(); }}>Previous</button>
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
                    {[person.account_username !== person.display_name ? person.account_username : "", person.email].filter(Boolean).join(" · ")}
                  </div>
                  {!person.matched && <span className="chip warn">Unmatched</span>}
                </td>
                <td className="col-requests" data-label="Requests"><span className="cell-value">{person.request_count}</span></td>
                <td className="col-library" data-label="In library"><span className="cell-value">{person.library_count}</span></td>
                <td className="col-plays" data-label="Plays"><span className="cell-value">{person.play_count}</span></td>
                <td className="col-size" data-label="Requested size"><span className="cell-value">{bytes(person.library_size)}</span></td>
                <td className="col-watched" data-label="Last watched" title={whenFull(person.last_watched_at)}><span className="cell-value">{when(person.last_watched_at)}</span></td>
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

function Whitelist({ onOpenLibrary }: { onOpenLibrary: (q: string) => void }) {
  const [items, setItems] = useState<WhitelistItem[]>([]);
  const [pattern, setPattern] = useState("");
  const [note, setNote] = useState("");
  const [matchType, setMatchType] = useState("title");
  const [mediaType, setMediaType] = useState("any");
  const [open, setOpen] = useState<Set<number>>(new Set());

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

  function toggle(id: number) {
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function ruleLabel(item: WhitelistItem) {
    if (item.match_type === "id") return `TMDB ${item.tmdb_id || item.pattern}`;
    return item.pattern;
  }

  function ruleHint(item: WhitelistItem) {
    const parts = [
      item.match_type === "id" ? "TMDB id" : "Title contains",
      item.media_type === "any" ? "any type" : item.media_type,
    ];
    if (item.note) parts.push(item.note);
    return parts.join(" · ");
  }

  return (
    <div className="page">
      <h2>Whitelist</h2>
      <p className="page-intro muted">
        Title matches are case-insensitive substrings. “Stargate” or “Back to the Future” protects the franchise. You can also whitelist a title straight from the library list.
      </p>
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
        {items.map((item) => {
          const matches = item.matches || [];
          const count = item.match_count ?? matches.length;
          const expanded = open.has(item.id);
          const preview = matches.slice(0, expanded ? matches.length : 4);
          return (
            <div className="list-item whitelist-item" key={item.id}>
              <div className="whitelist-main">
                <div className="whitelist-head">
                  <strong>{ruleLabel(item)}</strong>
                  <span className={`chip ${count ? "ok" : ""}`}>{count} match{count === 1 ? "" : "es"}</span>
                </div>
                <div className="muted">{ruleHint(item)}</div>
                {count > 0 ? (
                  <div className="whitelist-matches">
                    {preview.map((match) => (
                      <button
                        key={match.id}
                        type="button"
                        className="whitelist-match"
                        onClick={() => onOpenLibrary(match.title)}
                        title={`Open ${match.title} in the library`}
                      >
                        <span>{match.title}{match.year ? ` (${match.year})` : ""}</span>
                        <span className="muted">{match.media_type === "tv" ? "TV" : "Movie"}</span>
                      </button>
                    ))}
                    {count > preview.length ? (
                      <button type="button" className="linkish" onClick={() => toggle(item.id)}>
                        Show {count - preview.length} more
                      </button>
                    ) : null}
                    {expanded && count > 4 ? (
                      <button type="button" className="linkish" onClick={() => toggle(item.id)}>Show less</button>
                    ) : null}
                    <button
                      type="button"
                      className="linkish"
                      onClick={() => onOpenLibrary(item.match_type === "id" ? (matches[0]?.title || item.pattern) : item.pattern)}
                    >
                      Open in library
                    </button>
                  </div>
                ) : (
                  <div className="muted whitelist-empty">No library titles match this rule yet.</div>
                )}
              </div>
              <button className="ghost" onClick={async () => { await api.removeWhitelist(item.id); await load(); }}>Remove</button>
            </div>
          );
        })}
        {!items.length && <p className="muted">Nothing protected yet.</p>}
      </div>
    </div>
  );
}

function GithubIcon() {
  return (
    <svg className="inline-icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38l-.01-1.49c-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.4 7.4 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48l-.01 2.2c0 .21.15.46.55.38A8 8 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

const GITHUB_REPO = "https://github.com/michaelmurfy/cleanarr";

function About() {
  return (
    <section className="settings-section">
      <h3>About</h3>
      <div className="settings-actions about-links">
        <a className="ghost link-button" href={GITHUB_REPO} target="_blank" rel="noreferrer">
          <GithubIcon /> GitHub repository
        </a>
      </div>
    </section>
  );
}

function Settings() {
  const [values, setValues] = useState<Record<string, string>>({});
  const [flags, setFlags] = useState<Record<string, boolean>>({});
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [usernameLocked, setUsernameLocked] = useState(false);
  const [hideSettings, setHideSettings] = useState(false);
  const [defaultPassword, setDefaultPassword] = useState(false);
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
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const savedFlashTimer = useRef<number | null>(null);  const services = [
    { id: "tautulli", label: "Tautulli", urlKey: "tautulli_url" },
    { id: "tracearr", label: "Tracearr", urlKey: "tracearr_url" },
    { id: "jellystat", label: "Jellystat", urlKey: "jellystat_url" },
    { id: "seerr", label: "Seerr", urlKey: "seerr_url" },
    { id: "radarr", label: "Radarr", urlKey: "radarr_url" },
    { id: "radarr_4k", label: "Radarr 4K", urlKey: "radarr_4k_url" },
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
      copy: "Used to list titles, sizes, ratings, and delete files from disk. Optional second Radarr for a 4K library.",
      fields: [
        ["radarr_url", "Radarr URL"],
        ["radarr_api_key", "Radarr API key"],
        ["radarr_4k_url", "Radarr 4K URL"],
        ["radarr_4k_api_key", "Radarr 4K API key"],
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
        ["radarr_4k_external_url", "Radarr 4K public URL"],
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
    setDefaultPassword(Boolean(data.using_default_password));
    setScheduleEnabled((next.sync_schedule_enabled || "0") === "1");
    setIntervalHours(next.sync_interval_hours || "24");
    setAutoDelete((next.auto_delete_enabled || "0") === "1");
    setAutoDeleteCap(next.auto_delete_max_per_run || "10");
    setAutoDeleteDays(next.auto_delete_stale_days || "365");
    if (data.maintenance) setMaintenance(data.maintenance);
  }

  useEffect(() => {
    loadSettings().catch((err) => setError(err instanceof Error ? err.message : "Could not load settings"));
    return () => {
      if (savedFlashTimer.current) window.clearTimeout(savedFlashTimer.current);
    };
  }, []);

  function flashSaved(note: string) {
    setMessage(note);
    setSavedFlash(true);
    if (savedFlashTimer.current) window.clearTimeout(savedFlashTimer.current);
    savedFlashTimer.current = window.setTimeout(() => setSavedFlash(false), 2500);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setError("");
    setMessage("");
    setSaving(true);
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
    try {
      await api.saveSettings({
        values: outgoing,
        username: hideSettings || usernameLocked ? null : username,
        password: hideSettings || usernameLocked ? null : password || null,
      });
      setPassword("");
      await loadSettings();
      flashSaved(hideSettings ? "Schedule saved." : "Settings saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save settings");
      setSavedFlash(false);
    } finally {
      setSaving(false);
    }
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
          placeholder={secret ? (configured ? "Configured, leave blank" : "API key") : "https://"}
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
      {defaultPassword && (
        <p className="warn-banner" role="alert">
          Cleanarr is still using the default password. Set a real one
          {hideSettings ? " in your .env file (CLEANARR_PASSWORD) and restart." : " under Account below."}
        </p>
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
            <div className="settings-save">
              <button className="primary" type="submit" disabled={saving}>
                {saving ? "Saving…" : savedFlash ? "Saved" : "Save"}
              </button>
              {savedFlash && <span className="ok-message" role="status">{message || "Saved."}</span>}
            </div>
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
            <button className="primary" type="submit" disabled={saving}>
              {saving ? "Saving…" : savedFlash ? "Saved" : "Save settings"}
            </button>
            {savedFlash && <span className="ok-message" role="status">{message || "Saved."}</span>}
          </div>
        )}
        {hideSettings && savedFlash && (
          <p className="ok-message" role="status">{message || "Saved."}</p>
        )}
      </form>
      <About />
    </div>
  );
}
