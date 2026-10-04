export type DeploymentStatus = "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED";
export type StageStatus = "PENDING" | "RUNNING" | "SUCCESS" | "FAILED" | "SKIPPED";
export type EnvironmentName = "development" | "staging" | "production";

export interface User {
  id: number;
  username: string;
  role: "admin" | "user";
}

export interface Stage {
  name: string;
  position: number;
  status: StageStatus;
  exit_code: number | null;
  attempts: number;
  started_at: string | null;
  finished_at: string | null;
}

export interface Deployment {
  id: number;
  project_id: number;
  environment: EnvironmentName;
  commit: string;
  branch: string;
  author: string;
  trigger: "manual" | "webhook" | "rollback";
  status: DeploymentStatus;
  image: string;
  rollback_of: number | null;
  error: string;
  attempts: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  stages: Stage[];
}

export interface Environment {
  name: EnvironmentName;
  branch: string;
  health_url: string;
  port: number;
  auto_deploy: boolean;
  last_deployment: Deployment | null;
}

export interface Project {
  id: number;
  name: string;
  owner_id: number;
  repo_url: string;
  pipeline: Record<string, unknown>[];
  webhook_secret: string;
  created_at: string;
  environments: Environment[];
}

export interface Variable {
  key: string;
  is_secret: boolean;
  value: string; // masked by the API for secrets
}

export interface LogLine {
  id: number;
  stage: string;
  line: string;
  ts: string;
}

export type StreamEvent =
  | { type: "log"; id: number; stage: string; line: string; ts: string }
  | { type: "stage"; name: string; status: StageStatus; exit_code: number | null }
  | { type: "deployment"; status: DeploymentStatus };

export interface NewProject {
  name: string;
  repo_url: string;
  environments: { name: EnvironmentName; branch: string; health_url?: string; port?: number }[];
}
