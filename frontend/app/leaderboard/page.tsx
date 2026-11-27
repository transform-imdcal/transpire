import type { Metadata } from "next";
import { LeaderboardWorkspace } from "./leaderboard-workspace";

export const metadata: Metadata = {
  title: "Leaderboard | TRANSPIRE",
  description: "The people and sites delivering the most improvement value.",
};

export default function LeaderboardPage() {
  return <LeaderboardWorkspace />;
}
