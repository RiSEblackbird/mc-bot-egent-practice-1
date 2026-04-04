#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
plugin_dir="$repo_root/bridge-plugin"

if ! command -v java >/dev/null 2>&1; then
  printf 'Java 21 is required to build AgentBridge.\n' >&2
  printf 'On macOS, install openjdk@21 with Homebrew and add it to PATH.\n' >&2
  exit 1
fi

java_major="$(
  java -version 2>&1 | awk -F '[\".]' '/version/ { print $2; exit }'
)"
if [[ -z "$java_major" || "$java_major" -lt 21 ]]; then
  printf 'Java 21+ is required to build AgentBridge. Current runtime: %s\n' "$(java -version 2>&1 | head -n 1)" >&2
  exit 1
fi

if [[ -x "$plugin_dir/gradlew" ]]; then
  gradle_cmd=("$plugin_dir/gradlew")
elif command -v gradle >/dev/null 2>&1; then
  gradle_cmd=("gradle")
else
  printf 'Gradle or ./gradlew is required to build AgentBridge.\n' >&2
  printf 'On macOS, install Gradle with Homebrew or add a Gradle wrapper to bridge-plugin/.\n' >&2
  exit 1
fi

cd "$plugin_dir"
exec "${gradle_cmd[@]}" shadowJar
