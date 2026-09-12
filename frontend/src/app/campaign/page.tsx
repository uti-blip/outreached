import { redirect } from "next/navigation";
import { hasPageSession } from "@/lib/auth";
import CampaignWorkspace from "./workspace";

export const dynamic = "force-dynamic";

export default async function CampaignPage() {
  if (!await hasPageSession()) redirect("/login");
  return <CampaignWorkspace />;
}
