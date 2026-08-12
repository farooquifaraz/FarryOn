import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./lib/auth";
import Layout from "./components/Layout";
import Audit from "./pages/Audit";
import Billing from "./pages/Billing";
import Dashboard from "./pages/Dashboard";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Roles from "./pages/Roles";
import Users from "./pages/Users";

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          {/* `/` is the public front door; everything the admin panel owns
              lives under /admin, behind Layout's auth gate. */}
          <Route path="/" element={<Landing />} />
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
