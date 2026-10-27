import { useEffect, useState } from "react";
import { api } from "../api";

export function FileDialog({ title, onPick, onClose, start, dirsOnly }: { title: string; onPick: (path: string) => void; onClose: () => void; start?: string; dirsOnly?: boolean }) {
  const [path, setPath] = useState(start ?? "");
  const [listing, setListing] = useState<{ path: string; parent: string | null; entries: { name: string; path: string; dir: boolean; size?: number }[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    api.fs(path || undefined).then((l) => { setListing(l); setTyped(l.path); setError(null); }).catch((e) => setError(String(e.message)));
  }, [path]);

  const gb = (n?: number) => (n ? `${(n / 1e9).toFixed(1)} GB` : "");
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header><h2>{title}</h2><span className="dim">{error ?? ""}</span></header>
        <div className="path">
          <button className="btn small" disabled={listing?.parent === null} onClick={() => setPath(listing?.parent ?? "")}>Up</button>
          <input className="text" style={{ flex: 1 }} value={typed} onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { setPath(typed); } }} placeholder="Type or paste a path and press Enter" />
        </div>
        <div className="list">
          {listing?.entries.filter((en) => !dirsOnly || en.dir).map((en) => (
            <div key={en.path} className="entry" onDoubleClick={() => (en.dir ? setPath(en.path) : onPick(en.path))} onClick={() => (en.dir ? setPath(en.path) : onPick(en.path))}>
              <span className={en.dir ? "accent" : "dim"}>{en.dir ? ">" : "·"}</span>
              <span>{en.name}</span>
              <span className="dim num">{gb(en.size)}</span>
            </div>
          ))}
          {listing && listing.entries.length === 0 && <div className="entry dim">Nothing here</div>}
        </div>
        <footer>
          {dirsOnly && <button className="btn primary" onClick={() => onPick(listing?.path ?? typed)}>Use this folder</button>}
          <button className="btn" onClick={onClose}>Cancel</button>
        </footer>
      </div>
    </div>
  );
}
