import { BrowserRouter, Route, Routes } from "react-router-dom";
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
          <Route path="/login" element={<Login />} />
          <Route element={<Layout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/users" element={<Users />} />
            <Route path="/roles" element={<Roles />} />
            <Route path="/billing" element={<Billing />} />
            <Route path="/audit" element={<Audit />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
