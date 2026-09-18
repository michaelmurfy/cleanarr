import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, LogItem, MediaItem, Person, SyncStatus, WhitelistItem } from "./api";
import { Brand } from "./Logo";

type Page = "library" | "users" | "whitelist" | "logs" | "settings";

const FILTERS_KEY = "cleanarr.library";

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

  useEffect(() => {
    api.syncStatus().then(setSync).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (sync.status !== "running") return;
    const timer = setInterval(() => {
      api.syncStatus().then(setSync).catch(() => undefined);
    }, 800);
    return () => clearInterval(timer);
  }, [sync.status]);

  return (
    <div className="shell">
      <header className="topbar">
        <Brand compact />
        <nav className="nav">
          <button className={page === "library" ? "active" : ""} onClick={() => setPage("library")}>Library</button>
          <button className={page === "users" ? "active" : ""} onClick={() => setPage("users")}>Users</button>
          <button className={page === "whitelist" ? "active" : ""} onClick={() => setPage("whitelist")}>Whitelist</button>
          <button className={page === "logs" ? "active" : ""} onClick={() => setPage("logs")}>Logs</button>
          <button className={page === "settings" ? "active" : ""} onClick={() => setPage("settings")}>Settings</button>
        </nav>
        <div className="spacer" />
        <div className="topbar-sync">
          <span className={`sync-status ${sync.status === "running" ? "live" : ""}`} title={sync.message || "Idle"}>
            {sync.status === "running" && <span className="spinner" aria-hidden="true" />}
            <span className="sync-copy">{sync.status === "running" ? (sync.message || "Syncing…") : (sync.message || "Idle")}</span>
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
      {sync.status === "running" && <SyncBanner sync={sync} />}
      {page === "library" && <Library sync={sync} setSync={setSync} />}
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

function SyncBanner({ sync }: { sync: SyncStatus }) {
  const determinate = sync.percent != null && sync.total;
  return (
    <div className="sync-banner">
      <div className="sync-banner-copy">
        <span className="spinner" aria-hidden="true" />
        <strong>Syncing</strong>
        <span className="muted">{sync.message || "Working…"}</span>
        {determinate ? <span className="muted">{sync.percent}%</span> : null}
      </div>
      <div className={`progress ${determinate ? "" : "indeterminate"}`}>
        <span style={determinate ? { width: `${sync.percent}%` } : undefined} />
      </div>
    </div>
  );
}

function Library({ sync, setSync }: { sync: SyncStatus; setSync: (value: SyncStatus) => void }) {
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
        <button className={`stat ${filters.watched === "never" ? "active" : ""}`} onClick={() => patch(filters.watched === "never" ? { watched: "" } : { watched: "never", sort: "size" })}>
          <span className="muted">Never watched</span><b>{stats.never_watched ?? 0}</b>
        </button>
        <button className={`stat ${filters.watched === "stale" ? "active" : ""}`} onClick={() => patch(filters.watched === "stale" ? { watched: "" } : { watched: "stale", sort: "oldest" })}>
          <span className="muted">Stale / unwatched</span><b>{stats.stale ?? 0}</b>
        </button>
        <div className="stat" style={{ cursor: "default" }}>
          <span className="muted">Protected</span><b>{stats.whitelisted ?? 0}</b>
        </div>
      </div>
      <div className="filters">
        <button className={`chip-btn ${filters.watched === "never" ? "active" : ""}`} onClick={() => patch(filters.watched === "never" ? { watched: "" } : { watched: "never", sort: "size" })}>Never watched</button>
        <button className={`chip-btn ${filters.watched === "stale" && filters.sort === "oldest" ? "active" : ""}`} onClick={() => patch(filters.watched === "stale" ? { watched: "" } : { watched: "stale", sort: "oldest" })}>Oldest / stale</button>
        <button className={`chip-btn ${filters.sort === "rating" ? "active" : ""}`} onClick={() => patch({ sort: filters.sort === "rating" ? "oldest" : "rating" })}>Lowest rated</button>
        <button className={`chip-btn ${filters.sort === "size" ? "active" : ""}`} onClick={() => patch({ sort: filters.sort === "size" ? "oldest" : "size" })}>Largest</button>
      </div>
      <div className="filters">
        <input type="search" placeholder="Search title, requester, watcher" value={qInput} onChange={(e) => setQInput(e.target.value)} />
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
              <th>
                <input
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
              <tr key={item.id}>
                <td>
                  <input type="checkbox" disabled={item.whitelisted} checked={selected.has(item.id)} onChange={() => toggle(item.id)} />
                </td>
                <td>
                  <div className="title-cell">
                    {item.art_url ? <img className="poster" src={item.art_url} alt="" /> : <div className="poster placeholder">No art</div>}
                    <div>
                      <strong>{item.title}</strong> {item.year ? <span className="muted">({item.year})</span> : null}
                      <div className="muted">{item.media_type === "movie" ? "Movie" : "TV"}{item.sources.length ? ` · ${item.sources.join(" / ")}` : ""}</div>
                      {item.whitelisted && <span className="chip warn">Protected · {item.whitelist_reason}</span>}
                    </div>
                  </div>
                </td>
                <td className="rating" title={item.rating_source ? `${item.rating_source} · ${item.rating_votes} votes` : "No rating"}>
                  {item.rating != null ? <><strong>{Number(item.rating).toFixed(1)}</strong> <span className="muted">/10</span></> : "—"}
                </td>
                <td title={whenFull(item.last_watched_at)}>{when(item.last_watched_at)}</td>
                <td>{item.play_count}</td>
                <td className="watchers" title={item.watchers.map((watcher) => `${watcher.user} ×${watcher.plays}`).join(", ")}>
                  {item.watchers.length
                    ? `${item.watchers.slice(0, 2).map((watcher) => `${watcher.user} ×${watcher.plays}`).join(", ")}${item.watchers.length > 2 ? ` +${item.watchers.length - 2}` : ""}`
                    : "—"}
                </td>
                <td>{item.requested_by || "—"}</td>
                <td>{bytes(item.size_bytes)}</td>
                <td>
                  <div className="row-actions">
                    {!item.whitelisted && <button className="ghost" onClick={() => keep(item)}>Whitelist</button>}
                    {item.links.seerr && <a href={item.links.seerr} target="_blank" rel="noreferrer">Seerr</a>}
                    {item.links.tautulli && <a href={item.links.tautulli} target="_blank" rel="noreferrer">Tautulli</a>}
                    {item.links.tracearr && <a href={item.links.tracearr} target="_blank" rel="noreferrer">Tracearr</a>}
                    {item.links.radarr && <a href={item.links.radarr} target="_blank" rel="noreferrer">Radarr</a>}
                    {item.links.sonarr && <a href={item.links.sonarr} target="_blank" rel="noreferrer">Sonarr</a>}
                  </div>
                </td>
              </tr>
            ))}
            {!items.length && (
              <tr>
                <td colSpan={9} className="empty">{loading ? "Loading library…" : "Nothing matches these filters. Try Never watched or Oldest / stale."}</td>
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
      <p className="muted">Sync progress, unmatched watch-history titles, and deletions. Newest first.</p>
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

  return (
    <div className="page">
      <h2>Users</h2>
      <p className="muted">Seerr requesters and Tautulli/Tracearr watchers are matched to Plex usernames when those exist.</p>
      <div className="stats">
        <div className="stat" style={{ cursor: "default" }}><span className="muted">People</span><b>{stats.users ?? 0}</b></div>
        <div className="stat" style={{ cursor: "default" }}><span className="muted">Requests in library</span><b>{stats.requests ?? 0}</b></div>
        <div className="stat" style={{ cursor: "default" }}><span className="muted">Plays</span><b>{stats.plays ?? 0}</b></div>
        <div className="stat" style={{ cursor: "default" }}><span className="muted">Titles listed</span><b>{stats.library ?? 0}</b></div>
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
            {items.map((person) => (
              <tr key={person.canonical}>
                <td>
                  <strong>{person.display_name}</strong>
                  <div className="muted">
                    {person.plex_username && person.plex_username !== person.display_name ? `Plex · ${person.plex_username}` : person.plex_username ? "Plex user" : "No Plex username"}
                    {person.email ? ` · ${person.email}` : ""}
                  </div>
                </td>
                <td>{person.request_count}</td>
                <td>{person.library_count}</td>
                <td>{person.play_count}</td>
                <td>{bytes(person.library_size)}</td>
                <td title={whenFull(person.last_watched_at)}>{when(person.last_watched_at)}</td>
                <td>
                  <div className="row-actions">
                    <button className="ghost" onClick={() => onOpenLibrary(person.display_name)}>Library</button>
                    {person.links.seerr && <a href={person.links.seerr} target="_blank" rel="noreferrer">Seerr</a>}
                    {person.links.tautulli && <a href={person.links.tautulli} target="_blank" rel="noreferrer">Tautulli</a>}
                  </div>
                </td>
              </tr>
            ))}
            {!items.length && (
              <tr><td colSpan={7} className="empty">No users yet. Sync the library to pull Seerr and Tautulli people.</td></tr>
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
  const [envFile, setEnvFile] = useState(false);
  const [message, setMessage] = useState("");
  const groups = [
    {
      title: "Watch history",
      copy: "Tautulli covers Plex. Tracearr covers Jellyfin, Plex, or Emby. Both can be enabled; Cleanarr dedupes overlapping plays.",
      fields: [
        ["tautulli_url", "Tautulli URL"],
        ["tautulli_api_key", "Tautulli API key"],
        ["tracearr_url", "Tracearr URL"],
        ["tracearr_api_key", "Tracearr API key"],
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
      ],
    },
  ] as const;

  useEffect(() => {
    api.settings().then((data) => {
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
      setEnvFile(data.env_file);
    });
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (envFile) return;
    const outgoing: Record<string, string> = {};
    for (const [key, value] of Object.entries(values)) {
      if (key.endsWith("_api_key") && !value) continue;
      outgoing[key] = value;
    }
    await api.saveSettings({ values: outgoing, username, password: password || null });
    setPassword("");
    setMessage("Saved.");
  }

  async function test(service: string) {
    try {
      const result = await api.test(service);
      setMessage(`${service}: ${result.message}`);
    } catch (err) {
      setMessage(`${service}: ${err instanceof Error ? err.message : "failed"}`);
    }
  }

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

  return (
    <div className="page">
      <h2>Settings</h2>
      {envFile && <p className="muted">A <code>.env</code> file is present, so service fields are read-only. Change them there and restart the container.</p>}
      {!envFile && <p className="muted">Values present in the process environment are locked. API keys are never shown after they are saved.</p>}
      <form onSubmit={save}>
        {groups.map((group) => (
          <section className="settings-section" key={group.title}>
            <h3>{group.title}</h3>
            <p className="muted">{group.copy}</p>
            <div className="form-grid">{group.fields.map(([key, label]) => field(key, label))}</div>
          </section>
        ))}
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
        <div className="filters">
          <button className="primary" type="submit" disabled={envFile}>Save unlocked fields</button>
          <button className="ghost" type="button" onClick={() => test("tautulli")}>Tautulli</button>
          <button className="ghost" type="button" onClick={() => test("tracearr")}>Tracearr</button>
          <button className="ghost" type="button" onClick={() => test("seerr")}>Seerr</button>
          <button className="ghost" type="button" onClick={() => test("radarr")}>Radarr</button>
          <button className="ghost" type="button" onClick={() => test("sonarr")}>Sonarr</button>
        </div>
      </form>
      {message && <p className="muted">{message}</p>}
    </div>
  );
}
