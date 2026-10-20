export type Status = "fail" | "warn" | "pass" | "info";

export interface Finding {
  id: string;
  category: string;
  title: string;
  status: Status;
  severity: "high" | "medium" | "low";
  law: string;
  summary: string;
  recommendation: string;
  penalty: string;
  evidence: string[];
  evidence_total: number;
}

export interface ProfileForm {
  kind: string;
  kind_name: string;
  page: string;
  fields: string[];
  pd: boolean;
  special: boolean;
  card: boolean;
  consent_checkbox: boolean;
  ad_consent_checkbox: boolean;
  screenshot?: string | null;
}

export interface Profile {
  types: { code: string; name: string; score: number; signals: string[] }[];
  primary_type: string;
  platform: { name: string; foreign: boolean } | null;
  features: Record<string, string>;
  payments: string[];
  chat: string[];
  forms: ProfileForm[];
  restricted: Record<string, string[]>;
  pages_count: number;
}

export interface Report {
  profile?: Profile;
  score: number;
  grade: "good" | "medium" | "bad";
  counts: Record<Status, number>;
  final_url: string;
  pages: { url: string; title: string; status: number | null; screenshot?: string | null }[];
  server: { ip: string | null; country: string | null };
  findings: Finding[];
  disclaimer: string;
}

export interface Scan {
  id: string;
  url: string;
  host: string;
  status: "queued" | "running" | "done" | "failed";
  pages_scanned: number;
  max_pages: number;
  current_url: string | null;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  report: Report | null;
}
