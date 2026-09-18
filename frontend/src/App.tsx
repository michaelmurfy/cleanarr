import { FormEvent, useEffect, useMemo, useState } from "react";
import { api, MediaItem, WhitelistItem } from "./api";

type Page = "library" | "whitelist" | "settings";

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
  return new Date(ts * 1000).toLocaleString();
}

export function App() {
  const [user, setUser] = useState<string | null>(null);
  const [booting, setBooting] = useState(true);

  useEffect(() => {
    api
      .me()
      .then((me) => setUser(me.username))
      .catch(() => setUser(null))
      .finally(() => setBooting(false));
  }, []);

  if (booting) return <div className="login"><p className="muted">Loading Cleanarr…</p></div>;
  if (!user) return <Login onDone={setUser} />;
  return <Shell user={user} onLogout={() => setUser(null)} />;
}

function Login({ onDone }: { onDone: (user: string) => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const result = await api.login(username, password) as { username: string };
      onDone(result.username);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <div className="brand">Clean<span>arr</span></div>
        <h1>Sign in</h1>
        <p className="muted">Watch history in, leftover library out.</p>
        <div className="stack">
          <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} /></label>
          <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        </div>
        {error && <p className="error">{error}</p>}
        <button className="primary" type="submit">Continue</button>
      </form>
    </div>
  );
}

function Shell({ user, onLogout }: { user: string; onLogout: () => void }) {
  const [page, setPage] = useState<Page>("library");
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">Clean<span>arr</span></div>
        <nav className="nav">
          <button className={page === "library" ? "active" : ""} onClick={() => setPage("library")}>Library</button>
          <button className={page === "whitelist" ? "active" : ""} onClick={() => setPage("whitelist")}>Whitelist</button>
          <button className={page === "settings" ? "active" : ""} onClick={() => setPage("settings")}>Settings</button>
        </nav>
        <div className="spacer" />
        <span className="muted">{user}</span>
        <button className="ghost" onClick={async () => { await api.logout(); onLogout(); }}>Sign out</button>
      </header>
      {page === "library" && <Library />}
      {page === "whitelist" && <Whitelist />}
      {page === "settings" && <Settings />}
    </div>
  );
}

function Library() {
  const [items, setItems] = useState<MediaItem[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [sync, setSync] = useState({ status: "idle", message: "" });
  const [q, setQ] = useState("");
  const [mediaType, setMediaType] = useState("");
  const [watched, setWatched] = useState("");
  const [sort, setSort] = useState("last_watched");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  const [pending, setPending] = useState<null | { blacklist: boolean }>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    const data = await api.library({ q, media_type: mediaType, watched, sort });
    setItems(data.items);
    setStats(data.stats);
    setSync(data.sync);
  }

  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, [q, mediaType, watched, sort]);

  useEffect(() => {
    if (sync.status !== "running") return;
    const timer = setInterval(() => {
      api.syncStatus().then((status) => {
        setSync(status);
        if (status.status !== "running") load().catch(() => undefined);
      });
    }, 1500);
    return () => clearInterval(timer);
  }, [sync.status]);

  const selectedItems = useMemo(() => items.filter((item) => selected.has(item.id)), [items, selected]);
  const selectedSize = selectedItems.reduce((sum, item) => sum + (item.size_bytes || 0), 0);

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
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cleanup failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="stats">
        <div className="stat"><span className="muted">Titles</span><b>{stats.count ?? 0}</b></div>
        <div className="stat"><span className="muted">Never watched</span><b>{stats.never_watched ?? 0}</b></div>
        <div className="stat"><span className="muted">Whitelisted</span><b>{stats.whitelisted ?? 0}</b></div>
        <div className="stat"><span className="muted">Library size</span><b>{bytes(stats.size_bytes || 0)}</b></div>
      </div>
      <div className="filters">
        <input type="search" placeholder="Search title, requester, watcher" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={mediaType} onChange={(e) => setMediaType(e.target.value)}>
          <option value="">Movies & TV</option>
          <option value="movie">Movies</option>
          <option value="tv">TV</option>
        </select>
        <select value={watched} onChange={(e) => setWatched(e.target.value)}>
          <option value="">Any watch state</option>
          <option value="never">Never watched</option>
          <option value="watched">Watched</option>
          <option value="stale">Stale / unwatched</option>
        </select>
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="last_watched">Last watched</option>
          <option value="plays">Play count</option>
          <option value="size">Size</option>
          <option value="title">Title</option>
          <option value="requested">Requested by</option>
        </select>
        <div className="spacer" />
        <span className="muted">{sync.message || "Idle"}</span>
        <button className="primary" onClick={async () => { setSync(await api.sync()); }}>Sync now</button>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  checked={items.length > 0 && selected.size === items.length}
                  onChange={(e) => setSelected(e.target.checked ? new Set(items.map((item) => item.id)) : new Set())}
                />
              </th>
              <th>Title</th>
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
                  <input
                    type="checkbox"
                    disabled={item.whitelisted}
                    checked={selected.has(item.id)}
                    onChange={() => toggle(item.id)}
                  />
                </td>
                <td>
                  <div className="title-cell">
                    {item.poster_url ? <img className="poster" src={item.poster_url} alt="" /> : <div className="poster placeholder">No art</div>}
                    <div>
                      <strong>{item.title}</strong> {item.year ? <span className="muted">({item.year})</span> : null}
                      <div className="muted">{item.media_type === "movie" ? "Movie" : "TV"} {item.sources.map((source) => ` · ${source}`)}</div>
                      {item.whitelisted && <span className="chip warn">Protected · {item.whitelist_reason}</span>}
                    </div>
                  </div>
                </td>
                <td>{when(item.last_watched_at)}</td>
                <td>{item.play_count}</td>
                <td className="watchers">
                  {item.watchers.length
                    ? item.watchers.map((watcher) => `${watcher.user} ×${watcher.plays}`).join(", ")
                    : "—"}
                </td>
                <td>{item.requested_by || "—"}</td>
                <td>{bytes(item.size_bytes)}</td>
                <td>
                  <div className="row-actions">
                    {item.links.seerr && <a href={item.links.seerr} target="_blank" rel="noreferrer">Seerr</a>}
                    {item.links.radarr && <a href={item.links.radarr} target="_blank" rel="noreferrer">Radarr</a>}
                    {item.links.sonarr && <a href={item.links.sonarr} target="_blank" rel="noreferrer">Sonarr</a>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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
              This removes {selectedItems.length} title{selectedItems.length === 1 ? "" : "s"} from
              {" "}{selectedItems.some((item) => item.media_type === "movie") ? "Radarr" : ""}
              {selectedItems.some((item) => item.media_type === "movie") && selectedItems.some((item) => item.media_type === "tv") ? " / " : ""}
              {selectedItems.some((item) => item.media_type === "tv") ? "Sonarr" : ""}
              {pending.blacklist ? ", blacklists them in Seerr," : ""} and deletes the files.
              Whitelisted titles are skipped.
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
    await api.addWhitelist({ pattern, note, match_type: matchType, media_type: mediaType });
    setPattern("");
    setNote("");
    await load();
  }

  return (
    <div className="page">
      <h2>Whitelist</h2>
      <p className="muted">Title matches are case-insensitive substrings. “Stargate” or “Back to the Future” will protect the whole franchise.</p>
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
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const fields = [
    ["tautulli_url", "Tautulli URL"],
    ["tautulli_api_key", "Tautulli API key"],
    ["tracearr_url", "Tracearr URL"],
    ["tracearr_api_key", "Tracearr API key"],
    ["seerr_url", "Seerr / Jellyseerr / Overseerr URL"],
    ["seerr_api_key", "Seerr API key"],
    ["radarr_url", "Radarr URL"],
    ["radarr_api_key", "Radarr API key"],
    ["sonarr_url", "Sonarr URL"],
    ["sonarr_api_key", "Sonarr API key"],
    ["seerr_external_url", "Seerr public URL (optional)"],
    ["radarr_external_url", "Radarr public URL (optional)"],
    ["sonarr_external_url", "Sonarr public URL (optional)"],
  ] as const;

  useEffect(() => {
    api.settings().then((data) => {
      const next: Record<string, string> = {};
      for (const [key, value] of Object.entries(data.values)) {
        if (typeof value === "string") next[key] = value;
      }
      setValues(next);
      setUsername(data.username);
    });
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    await api.saveSettings({ values, username, password: password || null });
    setPassword("");
    setMessage("Saved.");
  }

  async function test(service: string) {
    try {
      const result = await api.test(service) as { message: string };
      setMessage(`${service}: ${result.message}`);
    } catch (err) {
      setMessage(`${service}: ${err instanceof Error ? err.message : "failed"}`);
    }
  }

  return (
    <div className="page">
      <h2>Settings</h2>
      <p className="muted">Add Tautulli and/or Tracearr. If both are present, Cleanarr dedupes the same play across Plex and Jellyfin.</p>
      <form onSubmit={save}>
        <div className="form-grid">
          {fields.map(([key, label]) => (
            <label key={key}>
              {label}
              <input
                type={key.includes("api_key") || key.includes("password") ? "password" : "url"}
                value={values[key] || ""}
                placeholder={key.includes("api_key") ? "API key" : "https://"}
                onChange={(e) => setValues((current) => ({ ...current, [key]: e.target.value }))}
              />
            </label>
          ))}
          <label>Cleanarr username<input value={username} onChange={(e) => setUsername(e.target.value)} /></label>
          <label>New password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Leave blank to keep" /></label>
        </div>
        <div className="filters">
          <button className="primary" type="submit">Save</button>
          <button className="ghost" type="button" onClick={() => test("tautulli")}>Test Tautulli</button>
          <button className="ghost" type="button" onClick={() => test("tracearr")}>Test Tracearr</button>
          <button className="ghost" type="button" onClick={() => test("seerr")}>Test Seerr</button>
          <button className="ghost" type="button" onClick={() => test("radarr")}>Test Radarr</button>
          <button className="ghost" type="button" onClick={() => test("sonarr")}>Test Sonarr</button>
        </div>
      </form>
      {message && <p className="muted">{message}</p>}
    </div>
  );
}
