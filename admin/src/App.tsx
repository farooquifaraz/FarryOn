import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./lib/auth";
import Layout from "./components/Layout";
import Audit from "./pages/Audit";
import Billing from "./pages/Billing";
import Dashboard from "./pages/Dashboard";
import Login from "./pages/Login";
import Roles from "./pages/Roles";
import Users from "./pages/Users";

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {/* `/` is the public marketing site, served by the backend
              (backend/app/web/router.py) — the gateway routes it there before
              this SPA is ever reached. This redirect only fires when the SPA
              is served on its own, e.g. `npm run dev`. */}
          <Route path="/" element={<Navigate to="/admin" replace />} />
          <Route path="/login" element={<Login />} />
          <Route element={<Layout />}>
            <Route path="/admin" element={<Dashboard />} />
            <Route path="/admin/users" element={<Users />} />
            <Route path="/admin/roles" element={<Roles />} />
            <Route path="/admin/billing" element={<Billing />} />
            <Route path="/admin/audit" element={<Audit />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
