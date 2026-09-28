import Dashboard from "@/components/dashboard";
import { authConfig } from "@/lib/auth";
export const dynamic = "force-dynamic";
export default function Page() {
  let remote = false;
  try { remote = authConfig().remote; } catch { return <main>Dashboard configuration is incomplete.</main>; }
  return <Dashboard remote={remote}/>;
}
