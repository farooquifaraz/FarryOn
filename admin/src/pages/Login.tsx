import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api, ApiRequestError } from "../lib/api";
import { NotAnAdminError, useAuth } from "../lib/auth";

export default function Login() {
  const { user, login, verify2fa } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pendingToken, setPendingToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  /** Forgot password: the backend mails a reset link (the same flow the
   *  app uses); the page never learns whether the address exists. */
  async function forgot() {
    setError(null);
    setNotice(null);
    if (!email) {
      setError("Type your email first, then click Forgot password.");
      return;
    }
    setBusy(true);
    try {
      await api("/api/v1/auth/forgot-password", { method: "POST", body: { email } });
      setNotice(`If ${email} has an account, a reset link is on its way. Check your inbox (and spam).`);
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Could not send the reset email. Try again.");
    } finally {
      setBusy(false);
    }
  }

  if (user) return <Navigate to="/admin" replace />;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (pendingToken) {
        await verify2fa(pendingToken, code);
        navigate("/admin");
        return;
      }
      const result = await login(email, password);
      if (result.twoFactorRequired) {
        setPendingToken(result.pendingToken!);
      } else {
        navigate("/admin");
      }
    } catch (err) {
      if (err instanceof NotAnAdminError) {
        // Their password was right — say so, rather than "wrong credentials",
        // which would send someone hunting for a typo that isn't there. Reset
        // to the email step so a 2FA prompt doesn't linger over a dead login.
        setPendingToken(null);
        setError(err.message);
      } else {
        setError(
          err instanceof ApiRequestError ? err.message : "Something went wrong. Try again.",
        );
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-box" onSubmit={handleSubmit}>
        <div className="brand">
          <img className="mark-img" src="/farry-icon.png" alt="" width={40} height={40} />
          <span>
            Farry<em>On</em>
          </span>
        </div>
        <h3>{pendingToken ? "Two-factor check" : "Admin sign in"}</h3>
        <p className="lead">
          {pendingToken
            ? "Enter the 6-digit code from your authenticator app, or a recovery code."
            : "Restricted to accounts with an admin role."}
        </p>
        {pendingToken ? (
          <div className="field">
            <label>Code</label>
            <input
              autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value.trim())}
              placeholder="123456"
              required
            />
          </div>
        ) : (
          <>
            <div className="field">
              <label>Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                required
              />
            </div>
            <div className="field">
              <label>Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </div>
          </>
        )}
        <button className="btn-primary" disabled={busy}>
          {busy ? "Signing in…" : pendingToken ? "Verify" : "Sign in"}
        </button>
        {!pendingToken && (
          <button
            type="button"
            className="link-btn"
            disabled={busy}
            onClick={() => void forgot()}
            style={{ background: "none", border: "none", color: "var(--tm)", fontSize: 12.5, marginTop: 12, cursor: "pointer", textDecoration: "underline", width: "100%" }}
          >
            Forgot password?
          </button>
        )}
        {notice && <div style={{ color: "var(--good, #7fe3c8)", fontSize: 12.5, marginTop: 10, textAlign: "center" }}>{notice}</div>}
        {error && <div className="error-text">{error}</div>}
        <div className="login-foot">JWT-protected · role checked on every request</div>
      </form>
    </div>
  );
}
