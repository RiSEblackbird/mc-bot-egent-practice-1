/* global React, ReactDOM */
import type { ReactNode } from "react";

type QueueInfo = {
  backlog: number;
  max_size: number | null;
};

type PlanSummary = {
  goal?: string;
  goal_category?: string;
  goal_priority?: string;
  constraint_count?: number;
  constraints?: string[];
};

type PerceptionInfo = {
  summary?: string | null;
  history?: unknown[];
};

type DashboardState = {
  generated_at: string;
  role?: { current?: string };
  queue?: QueueInfo;
  last_chat?: string | null;
  plan_summary?: PlanSummary;
  status?: Record<string, unknown>;
  perception?: PerceptionInfo;
  events?: unknown[];
  recent_reflections?: unknown[];
};

type FetchResult =
  | { kind: "ok"; data: DashboardState }
  | { kind: "error"; message: string; status?: number };

declare const React: typeof import("react");
declare const ReactDOM: typeof import("react-dom/client");

const { useEffect, useMemo, useState } = React;
const { createRoot } = ReactDOM;

const formatJSON = (value: unknown): string => {
  try {
    return JSON.stringify(value, null, 2);
  } catch (err) {
    return `failed to render json: ${String(err)}`;
  }
};

const fetchState = async (token: string | null): Promise<FetchResult> => {
  try {
    const resp = await fetch("/api/state", {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
    if (!resp.ok) {
      return { kind: "error", message: "failed to load state", status: resp.status };
    }
    const data = (await resp.json()) as DashboardState;
    return { kind: "ok", data };
  } catch (err) {
    return { kind: "error", message: `network error: ${String(err)}` };
  }
};

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="card">
      <div className="label">{label}</div>
      {children}
    </div>
  );
}

function useDashboardData(token: string | null) {
  const [data, setData] = useState<DashboardState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      const result = await fetchState(token);
      if (cancelled) return;
      if (result.kind === "ok") {
        setData(result.data);
        setError(null);
      } else {
        setError(result.status === 401 ? "認証が必要です。トークンを設定してください。" : result.message);
      }
    };
    run();
    const id = window.setInterval(run, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [token]);

  return { data, error };
}

function App() {
  const [token, setToken] = useState<string | null>(() => {
    const saved = localStorage.getItem("dashboardToken");
    return saved ? saved : null;
  });

  const { data, error } = useDashboardData(token);

  const queueText = useMemo(() => {
    const backlog = data?.queue?.backlog ?? 0;
    const max = data?.queue?.max_size ?? "unbounded";
    return `${backlog} / ${max}`;
  }, [data]);

  const handleSaveToken = () => {
    const value = window.prompt("ダッシュボードのトークンを入力してください（未設定なら空でOK）", token ?? "");
    if (value === null) return;
    const trimmed = value.trim();
    if (trimmed) {
      localStorage.setItem("dashboardToken", trimmed);
      setToken(trimmed);
    } else {
      localStorage.removeItem("dashboardToken");
      setToken(null);
    }
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
        <button onClick={handleSaveToken}>トークン設定</button>
        <span style={{ color: "#8ca0c8", fontSize: 12 }}>
          {token ? "トークン設定済み" : "トークン未設定（公開環境では設定してください）"}
        </span>
        {error ? <span style={{ color: "#ffb3b3" }}>{error}</span> : null}
      </div>
      <div className="cards">
        <Section label="Role">
          <div id="role">{data?.role?.current ?? "unknown"}</div>
        </Section>
        <Section label="Queue">
          <div id="queue">{queueText}</div>
        </Section>
        <Section label="Last Chat">
          <div id="last-chat">{data?.last_chat ?? "未記録"}</div>
        </Section>
        <Section label="Perception">
          <div id="perception">{data?.perception?.summary ?? "未記録"}</div>
        </Section>
        <Section label="Plan">
          <div id="plan">{data?.plan_summary?.goal ?? "(planなし)"}</div>
        </Section>
      </div>

      <h3>Recent Events</h3>
      <pre id="events">{formatJSON(data?.events)}</pre>

      <h3>Perception Snapshots</h3>
      <pre id="perception-history">{formatJSON(data?.perception?.history)}</pre>

      <h3>Reflections</h3>
      <pre id="reflections">{formatJSON(data?.recent_reflections)}</pre>
    </div>
  );
}

const container = document.getElementById("root");
if (container) {
  const root = createRoot(container);
  root.render(<App />);
}
