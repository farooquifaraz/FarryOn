import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ApiRequestError } from "../lib/api";
import { useAuth } from "../lib/auth";

export default function Login() {
  const { user, login, verify2fa } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pendingToken, setPendingToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (pendingToken) {
        await verify2fa(pendingToken, code);
        navigate("/");
        return;
      }
      const result = await login(email, password);
      if (result.twoFactorRequired) {
        setPendingToken(result.pendingToken!);
      } else {
        navigate("/");
      }
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-box" onSubmit={handleSubmit}>
        <div className="brand">
          <div className="mark" />
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
        {error && <div className="error-text">{error}</div>}
        <div className="login-foot">JWT-protected · role checked on every request</div>
      </form>
    </div>
  );
}
