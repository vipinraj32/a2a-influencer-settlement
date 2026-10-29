import type { ReactNode } from "react";
import { Link } from "react-router";

/** Link a creator's id or name to their profile (advertiser side). */
export function CreatorLink({ id, children }: { id: string; children?: ReactNode }) {
  return (
    <Link to={`/advertiser/creators/${encodeURIComponent(id)}`} onClick={(e) => e.stopPropagation()}>
      {children ?? id}
    </Link>
  );
}
