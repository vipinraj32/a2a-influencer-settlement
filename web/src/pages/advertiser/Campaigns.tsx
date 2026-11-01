import { Link } from "react-router";
import { api } from "../../api";
import { Breadcrumbs } from "../../components/Breadcrumbs";
import { ErrorText, Spinner } from "../../components/ui";
import { useLiveEvents, useResource } from "../../live";
import { Icon } from "../landing/parts";
import type { Deal } from "../../types";
import { CampaignList, loadDeals } from "./AdvertiserHome";
import "./dashboard.css";

/** Every campaign of this advertiser, with the dashboard's filters and search. */
export function Campaigns() {
  const campaigns = useResource(api.campaigns, "campaigns");
  const deals = useResource(loadDeals, "advertiser-deals");
  useLiveEvents((e) => e.type === "campaign" || e.type === "deal", () => {
    void campaigns.reload();
    void deals.reload();
  });
  const list = campaigns.data ?? [];
  const byCampaign = new Map<string, Deal[]>();
  for (const d of deals.data ?? []) byCampaign.set(d.campaign_id, [...(byCampaign.get(d.campaign_id) ?? []), d]);

  return (
    <div className="dash">
      <Breadcrumbs items={[{ label: "Dashboard", to: "/advertiser" }, { label: "All campaigns" }]} />
      <section className="dash-hero">
        <div>
          <h1>All campaigns</h1>
          <p className="dash-sub">
            {list.length} campaign{list.length === 1 ? "" : "s"}
          </p>
        </div>
        <div className="dash-hero-actions">
          <Link to="/advertiser/new" className="btn btn-primary">
            <Icon name="plus" size={18} /> New campaign
          </Link>
        </div>
      </section>
      <ErrorText error={campaigns.error ?? deals.error} />
      <section className="panel dash-campaigns">
        {campaigns.loading && !campaigns.data ? (
          <Spinner />
        ) : list.length === 0 ? (
          <p className="muted">
            No campaigns yet. <Link to="/advertiser/new">Start your first one</Link>.
          </p>
        ) : (
          <CampaignList campaigns={list} byCampaign={byCampaign} pageSize={10} />
        )}
      </section>
    </div>
  );
}
