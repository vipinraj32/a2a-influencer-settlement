import type { ReactNode } from "react";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router";
import { Layout } from "./components/Layout";
import { Spinner } from "./components/ui";
import { Account } from "./pages/Account";
import { AdvertiserHome } from "./pages/advertiser/AdvertiserHome";
import { AgentProfile } from "./pages/advertiser/AgentProfile";
import { CampaignView } from "./pages/advertiser/CampaignView";
import { Creators } from "./pages/advertiser/Creators";
import { NewCampaign } from "./pages/advertiser/NewCampaign";
import { AgentPage } from "./pages/influencer/AgentPage";
import { InfluencerHome } from "./pages/influencer/InfluencerHome";
import { Login } from "./pages/Login";
import { Onboarding } from "./pages/Onboarding";
import { useSession } from "./session";
import type { Role } from "./types";

/** Signed in, finished onboarding (unless `onboarding`), and the right role. */
function Guard({ role, onboarding, children }: { role?: Role; onboarding?: boolean; children: ReactNode }) {
  const { me, loading } = useSession();
  if (loading) return <Spinner />;
  if (!me) return <Navigate to="/login" replace />;
  if (!onboarding && !me.profile_complete) return <Navigate to="/onboarding" replace />;
  if (role && me.user.role !== role) return <Navigate to="/app" replace />;
  return <>{children}</>;
}

function Home() {
  const { me, loading } = useSession();
  if (loading) return <Spinner />;
  if (!me) return <Navigate to="/login" replace />;
  if (!me.profile_complete) return <Navigate to="/onboarding" replace />;
  return <Navigate to={me.user.role === "advertiser" ? "/advertiser" : "/influencer"} replace />;
}

const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  { path: "/", element: <Home /> },
  { path: "/app", element: <Home /> },
  {
    element: (
      <Guard onboarding>
        <Layout />
      </Guard>
    ),
    children: [
      { path: "/onboarding", element: <Onboarding /> },
      { path: "/account", element: <Account /> },
    ],
  },
  {
    element: (
      <Guard role="advertiser">
        <Layout />
      </Guard>
    ),
    children: [
      { path: "/advertiser", element: <AdvertiserHome /> },
      { path: "/advertiser/new", element: <NewCampaign /> },
      { path: "/advertiser/creators", element: <Creators /> },
      { path: "/advertiser/agent", element: <AgentProfile /> },
      { path: "/advertiser/campaigns/:id", element: <CampaignView /> },
    ],
  },
  {
    element: (
      <Guard role="influencer">
        <Layout />
      </Guard>
    ),
    children: [
      { path: "/influencer", element: <InfluencerHome /> },
      { path: "/influencer/agent", element: <AgentPage /> },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]);

export function App() {
  return <RouterProvider router={router} />;
}
