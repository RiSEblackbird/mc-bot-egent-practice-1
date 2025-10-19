package com.example.bridge.util;

import java.util.Objects;

import org.bukkit.configuration.file.FileConfiguration;

/**
 * AgentBridge プラグインの設定値を型安全に保持するコンフィグレーションモデル。
 * Bukkit の FileConfiguration は文字列中心の API で入力検証が弱いため、本クラスで
 * 範囲チェックとフォールバックを集中管理し、実行時エラーを防ぐ。
 */
public final class AgentBridgeConfig {

    private final String bindAddress;
    private final int port;
    private final String apiKey;
    private final TimeoutConfig timeout;
    private final CoreProtectConfig coreProtect;
    private final FrontierConfig frontier;
    private final SafetyConfig safety;
    private final LoggingConfig logging;
    private final LangGraphConfig langGraph;

    private AgentBridgeConfig(
            String bindAddress,
            int port,
            String apiKey,
            TimeoutConfig timeout,
            CoreProtectConfig coreProtect,
            FrontierConfig frontier,
            SafetyConfig safety,
            LoggingConfig logging,
            LangGraphConfig langGraph) {
        this.bindAddress = bindAddress;
        this.port = port;
        this.apiKey = apiKey;
        this.timeout = timeout;
        this.coreProtect = coreProtect;
        this.frontier = frontier;
        this.safety = safety;
        this.logging = logging;
        this.langGraph = langGraph;
    }

    public String bindAddress() {
        return bindAddress;
    }

    public int port() {
        return port;
    }

    public String apiKey() {
        return apiKey;
    }

    public TimeoutConfig timeout() {
        return timeout;
    }

    public CoreProtectConfig coreProtect() {
        return coreProtect;
    }

    public FrontierConfig frontier() {
        return frontier;
    }

    public SafetyConfig safety() {
        return safety;
    }

    public LoggingConfig logging() {
        return logging;
    }

    public LangGraphConfig langGraph() {
        return langGraph;
    }

    /**
     * Bukkit の設定ファイルから AgentBridgeConfig を構築する。
     * 必須項目が欠落している場合は例外を送出し、運用者に設定見直しを促す。
     */
    public static AgentBridgeConfig from(FileConfiguration raw) {
        Objects.requireNonNull(raw, "raw configuration");
        String bind = raw.getString("bind", "127.0.0.1");
        int port = clampPort(raw.getInt("port", 19071));
        String apiKey = raw.getString("api_key", "CHANGE_ME");
        TimeoutConfig timeout = new TimeoutConfig(Math.max(raw.getInt("timeouts.http_ms", 3000), 100));
        CoreProtectConfig coreProtectConfig =
                new CoreProtectConfig(Math.max(raw.getInt("coreprotect.lookup_seconds", 315360000), 1));
        FrontierConfig frontierConfig = new FrontierConfig(
                raw.getString("frontier.region_name_prefix", "bot_job_"),
                Math.max(raw.getInt("frontier.region_buffer", 1), 0),
                Math.max(raw.getInt("frontier.window_length", 8), 1));
        SafetyConfig safetyConfig = new SafetyConfig(
                Math.max(raw.getInt("safety.functional_near_radius", 4), 0),
                raw.getBoolean("safety.liquids_stop", true),
                Math.max(raw.getInt("safety.max_positions_per_request", 1024), 1));
        LoggingConfig loggingConfig = new LoggingConfig(raw.getBoolean("logging.debug", false));
        LangGraphConfig langGraphConfig = new LangGraphConfig(
                raw.getString("langgraph.retry_endpoint", ""),
                raw.getString("langgraph.api_key", ""),
                Math.max(raw.getInt("langgraph.timeout_ms", 2000), 250));
        return new AgentBridgeConfig(
                bind,
                port,
                apiKey,
                timeout,
                coreProtectConfig,
                frontierConfig,
                safetyConfig,
                loggingConfig,
                langGraphConfig);
    }

    private static int clampPort(int candidate) {
        if (candidate < 1 || candidate > 65535) {
            return 19071;
        }
        return candidate;
    }

    /** HTTP 関連のタイムアウト設定。 */
    public static final class TimeoutConfig {
        private final int httpMillis;

        TimeoutConfig(int httpMillis) {
            this.httpMillis = httpMillis;
        }

        public int httpMillis() {
            return httpMillis;
        }
    }

    /** CoreProtect に関連する設定値。 */
    public static final class CoreProtectConfig {
        private final int lookupSeconds;

        CoreProtectConfig(int lookupSeconds) {
            this.lookupSeconds = lookupSeconds;
        }

        public int lookupSeconds() {
            return lookupSeconds;
        }
    }

    /** フロンティア領域管理に関する設定値。 */
    public static final class FrontierConfig {
        private final String regionNamePrefix;
        private final int regionBuffer;
        private final int windowLength;

        FrontierConfig(String regionNamePrefix, int regionBuffer, int windowLength) {
            this.regionNamePrefix = Objects.requireNonNull(regionNamePrefix, "regionNamePrefix");
            this.regionBuffer = regionBuffer;
            this.windowLength = windowLength;
        }

        public String regionNamePrefix() {
            return regionNamePrefix;
        }

        public int regionBuffer() {
            return regionBuffer;
        }

        public int windowLength() {
            return windowLength;
        }
    }

    /** 安全対策に関する設定値。 */
    public static final class SafetyConfig {
        private final int functionalNearRadius;
        private final boolean liquidsStop;
        private final int maxPositionsPerRequest;

        SafetyConfig(int functionalNearRadius, boolean liquidsStop, int maxPositionsPerRequest) {
            this.functionalNearRadius = functionalNearRadius;
            this.liquidsStop = liquidsStop;
            this.maxPositionsPerRequest = maxPositionsPerRequest;
        }

        public int functionalNearRadius() {
            return functionalNearRadius;
        }

        public boolean liquidsStop() {
            return liquidsStop;
        }

        public int maxPositionsPerRequest() {
            return maxPositionsPerRequest;
        }
    }

    /** ロギングレベルの設定。 */
    public static final class LoggingConfig {
        private final boolean debug;

        LoggingConfig(boolean debug) {
            this.debug = debug;
        }

        public boolean debug() {
            return debug;
        }
    }

    /** LangGraph リトライ連携に関する設定値。 */
    public static final class LangGraphConfig {
        private final String retryEndpoint;
        private final String apiKey;
        private final int timeoutMillis;

        LangGraphConfig(String retryEndpoint, String apiKey, int timeoutMillis) {
            this.retryEndpoint = Objects.toString(retryEndpoint, "").trim();
            this.apiKey = Objects.toString(apiKey, "");
            this.timeoutMillis = timeoutMillis;
        }

        public String retryEndpoint() {
            return retryEndpoint;
        }

        public String apiKey() {
            return apiKey;
        }

        public int timeoutMillis() {
            return timeoutMillis;
        }

        public boolean enabled() {
            return !retryEndpoint.isEmpty();
        }
    }
}
