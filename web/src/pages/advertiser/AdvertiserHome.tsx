import { Link } from "react-router";
import { api } from "../../api";
import { CampaignBadge, Card, Empty, ErrorText, Spinner, Stat } from "../../components/ui";
import { usd, when } from "../../format";
import { useLiveEvents, useResource } from "../../live";

export function AdvertiserHome() {
  const campaigns = useResource(api.campaigns, "campaigns");
  const directory = useResource(api.directory, "directory");
  useLiveEvents((e) => e.type === "campaign", campaigns.reload);

  const list = campaigns.data ?? [];
  const live = list.filter((c) => !["done", "failed"].includes(c.status)).length;
  const budget = list.reduce((s, c) => s + Number(c.spec.budget), 0);

  return (
    <div className="stack">
      <div className="page-head">
        <h1>Campaigns</h1>
        <Link to="/advertiser/new" className="btn btn-primary">
          New campaign
        </Link>
      </div>
      <div className="stats">
        <Stat label="Campaigns" value={list.length} />
        <Stat label="In progress" value={live} />
        <Stat label="Total budget" value={usd(budget)} />
        <Stat label="Live creator agents" value={directory.data?.length ?? "…"} hint="Onboarded influencers whose agents are switched on" />
      </div>
      <Card>
        <ErrorText error={campaigns.error} />
        {campaigns.loading && !campaigns.data ? (
          <Spinner />
        ) : list.length === 0 ? (
          <Empty>
            No campaigns yet. <Link to="/advertiser/new">Launch your first one</Link>: your agent negotiates with every matching creator agent.
          </Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Niche</th>
                <th>Budget</th>
                <th>Creators</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {list.map((c) => (
                <tr key={c.spec.id}>
                  <td>
                    <Link to={`/advertiser/campaigns/${c.spec.id}`}>
                      <strong>{c.spec.advertiser}</strong> <span className="muted">{c.spec.brand_handle}</span>
                    </Link>
                    <div className="muted small mono">{c.spec.id}</div>
                  </td>
                  <td>{c.spec.niche}</td>
                  <td>{usd(c.spec.budget)}</td>
                  <td>up to {c.spec.max_creators}</td>
                  <td>
                    <CampaignBadge status={c.status} />
                    {c.error && <div className="error small">{c.error}</div>}
                  </td>
                  <td className="muted">{when(c.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
