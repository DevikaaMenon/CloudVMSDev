import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { className } from "../hooks";
import { errorText as apiErrorText } from "../api";

// ---------------------------------------------------------------- badges
export function StatusBadge({ status }: { status: string }) {
  const text = status === "REGISTERED" ? "Starting" : status.charAt(0) + status.slice(1).toLowerCase();
  return <span className={`badge st-${status}`}><i />{text}</span>;
}
export function SevBadge({ severity }: { severity: string }) {
  return <span className={`badge sev sev-${severity}`}>{severity.charAt(0).toUpperCase() + severity.slice(1)}</span>;
}
export function EventStatusBadge({ status }: { status: string }) {
  return <span className={`badge status-${status}`}><i />{status.charAt(0) + status.slice(1).toLowerCase()}</span>;
}
export function GroupTag({ cls }: { cls: string }) {
  const g = cls === "person" ? "person" : cls === "system" ? "" : "vehicle";
  return <span className={`badge ${g ? `tag-${g}` : ""} ${className(cls)}`}>{cls.replace(/_/g, " ")}</span>;
}

// ---------------------------------------------------------------- empty / error
export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="empty"><b>{title}</b>{children}</div>;
}
export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="error-box" role="alert">{apiErrorText(error)}</div>;
}

// ---------------------------------------------------------------- dialogs
export function Dialog({ title, onClose, children, footer, wide }:
  { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="scrim center" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" style={wide ? { width: "min(980px, 100%)" } : undefined}>
        <header><h2>{title}</h2><span className="spacer" /><button className="btn-quiet" onClick={onClose} aria-label="Close">✕</button></header>
        <div className="body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </div>
    </div>
  );
}

export function Drawer({ title, onClose, children, actions }:
  { title: ReactNode; onClose: () => void; children: ReactNode; actions?: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true">
        <header><h2>{title}</h2><span className="spacer" />{actions}<button className="btn-quiet" onClick={onClose} aria-label="Close">✕</button></header>
        <div className="body">{children}</div>
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------- toasts
interface Toast { id: number; text: ReactNode; kind: "info" | "attn" | "err" }
const ToastCtx = createContext<(text: ReactNode, kind?: Toast["kind"]) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: ReactNode, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x.slice(-3), { id, text, kind }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), kind === "err" ? 8000 : 5000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => <div key={t.id} className={`toast ${t.kind === "info" ? "" : t.kind}`}>{t.text}</div>)}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ---------------------------------------------------------------- pagination
export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="pager">
      <span>{total === 0 ? "No results" : `${(page - 1) * pageSize + 1}–${Math.min(total, page * pageSize)} of ${total}`}</span>
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <button disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </div>
  );
}
