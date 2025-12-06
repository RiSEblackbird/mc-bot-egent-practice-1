(() => {
  const { useState, useEffect, useMemo } = React;
  const { createRoot } = ReactDOM;

  const formatJSON = (value) => {
    try {
      return JSON.stringify(value, null, 2);
    } catch (err) {
      return `failed to render json: ${String(err)}`;
    }
  };

  const fetchState = async (token) => {
    try {
      const resp = await fetch("/api/state", {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (!resp.ok) {
        return { kind: "error", message: "failed to load state", status: resp.status };
      }
      const data = await resp.json();
      return { kind: "ok", data };
    } catch (err) {
      return { kind: "error", message: `network error: ${String(err)}` };
    }
  };

  function Section({ label, children }) {
    return React.createElement(
      "div",
      { className: "card" },
      React.createElement("div", { className: "label" }, label),
      children
    );
  }

  function useDashboardData(token) {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);

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
    const [token, setToken] = useState(() => {
      const saved = localStorage.getItem("dashboardToken");
      return saved ? saved : null;
    });

    const { data, error } = useDashboardData(token);

    const queueText = useMemo(() => {
      const backlog = (data && data.queue && data.queue.backlog) ?? 0;
      const max = (data && data.queue && data.queue.max_size) ?? "unbounded";
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

    return React.createElement(
      "div",
      null,
      React.createElement(
        "div",
        { style: { display: "flex", gap: 8, alignItems: "center", marginBottom: 12 } },
        React.createElement("button", { onClick: handleSaveToken }, "トークン設定"),
        React.createElement(
          "span",
          { style: { color: "#8ca0c8", fontSize: 12 } },
          token ? "トークン設定済み" : "トークン未設定（公開環境では設定してください）"
        ),
        error ? React.createElement("span", { style: { color: "#ffb3b3" } }, error) : null
      ),
      React.createElement(
        "div",
        { className: "cards" },
        React.createElement(
          Section,
          { label: "Role" },
          React.createElement("div", { id: "role" }, (data && data.role && data.role.current) ?? "unknown")
        ),
        React.createElement(
          Section,
          { label: "Queue" },
          React.createElement("div", { id: "queue" }, queueText)
        ),
        React.createElement(
          Section,
          { label: "Last Chat" },
          React.createElement("div", { id: "last-chat" }, (data && data.last_chat) ?? "未記録")
        ),
        React.createElement(
          Section,
          { label: "Perception" },
          React.createElement("div", { id: "perception" }, (data && data.perception && data.perception.summary) ?? "未記録")
        ),
        React.createElement(
          Section,
          { label: "Plan" },
          React.createElement("div", { id: "plan" }, (data && data.plan_summary && data.plan_summary.goal) ?? "(planなし)")
        )
      ),
      React.createElement("h3", null, "Recent Events"),
      React.createElement("pre", { id: "events" }, formatJSON(data && data.events)),
      React.createElement("h3", null, "Perception Snapshots"),
      React.createElement("pre", { id: "perception-history" }, formatJSON(data && data.perception && data.perception.history)),
      React.createElement("h3", null, "Reflections"),
      React.createElement("pre", { id: "reflections" }, formatJSON(data && data.recent_reflections))
    );
  }

  const container = document.getElementById("root");
  if (container) {
    const root = createRoot(container);
    root.render(React.createElement(App, null));
  }
})();
