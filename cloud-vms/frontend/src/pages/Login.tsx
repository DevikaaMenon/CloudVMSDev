import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../auth";
import { Logo } from "../components/Icons";
import { ErrorBox } from "../components/ui";

export default function Login() {
  const { user, login } = useAuth();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to="/" replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try { await login(username, password); } catch (err) { setError(err); } finally { setBusy(false); }
  };

  return (
    <div className="login">
      <section className="login-art">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}><Logo /><b style={{ color: "#fff", fontSize: 18 }}>Gatehouse</b></div>
        <div>
          <h1>Every camera at the gate, one place to watch it.</h1>
          <p>Live views, entry and exit counts for people and vehicles, and incidents with the video that proves them.</p>
        </div>
        <svg viewBox="0 0 600 120" style={{ width: "100%", opacity: .5 }} aria-hidden="true">
          <path d="M0 100h600" stroke="#e8a33d" strokeWidth="2" strokeDasharray="10 10" />
          <path d="M80 100V40l40-18 40 18v60M440 100V40l40-18 40 18v60" stroke="#c9d2e3" strokeWidth="2" fill="none" />
          <path d="M160 70h280" stroke="#c9d2e3" strokeWidth="6" />
        </svg>
      </section>
      <section className="login-form">
        <form onSubmit={submit}>
          <h2>Sign in</h2>
          <label className="field">Username<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus /></label>
          <label className="field">Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
          <ErrorBox error={error} />
          <button className="btn-primary" disabled={busy || !password} style={{ justifyContent: "center", padding: "10px" }}>{busy ? "Signing in" : "Sign in"}</button>
          <p className="hint">First run? The administrator password is in <code>data/initial_admin_password.txt</code>.</p>
        </form>
      </section>
    </div>
  );
}
