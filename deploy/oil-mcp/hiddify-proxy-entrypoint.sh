#!/bin/sh
set -eu

secret_file=/var/lib/hiddify/subscription-url
state_dir=/var/lib/hiddify
refresh_seconds=${HIDDIFY_REFRESH_SECONDS:-3600}
probe_url=https://www.gstatic.com/generate_204
core_pid=

if [ "$(id -u)" -eq 0 ]; then
  if [ ! -r "$secret_file" ]; then
    echo "Hiddify subscription secret is not readable" >&2
    exit 1
  fi
  chmod 600 "$secret_file"
  exec su-exec 65532:65532 env HIDDIFY_REFRESH_SECONDS="$refresh_seconds" \
    "$0" --read-secret-stdin < "$secret_file"
fi

if [ "${1:-}" != --read-secret-stdin ]; then
  echo "Hiddify proxy must start through its root privilege-drop wrapper" >&2
  exit 1
fi

subscription_url=
IFS= read -r subscription_url || true
if IFS= read -r extra_subscription_line; then
  echo "Hiddify subscription secret must contain exactly one line" >&2
  exit 1
fi

case "$refresh_seconds" in
  ''|*[!0-9]*|0)
    echo "HIDDIFY_REFRESH_SECONDS must be a positive integer" >&2
    exit 1
    ;;
esac

hiddify_core=$(find /hiddify -type f -name hiddify-core -perm -u+x -print | head -n 1)
if [ -z "$hiddify_core" ]; then
  echo "Hiddify Core executable was not found" >&2
  exit 1
fi

umask 077
mkdir -p "$state_dir/core"
work_dir=$(mktemp -d /tmp/hiddify-proxy.XXXXXX)
active_config=$state_dir/active.json
previous_config=$state_dir/previous.json
candidate_config=$work_dir/candidate.json
parsed_config=$work_dir/parsed.json
subscription_file=$work_dir/subscription

stop_core() {
  if [ -n "$core_pid" ] && kill -0 "$core_pid" 2>/dev/null; then
    kill "$core_pid"
    wait "$core_pid" 2>/dev/null || true
  fi
  core_pid=
}

cleanup() {
  stop_core
  rm -rf "$work_dir"
}

trap cleanup EXIT
trap 'exit 0' INT TERM

prepare_candidate() {
  case "$subscription_url" in
    http://*|https://*) ;;
    *)
      echo "Hiddify subscription secret must contain one HTTP or HTTPS URL" >&2
      return 1
      ;;
  esac

  rm -f "$subscription_file" "$parsed_config" "$candidate_config"
  {
    printf 'url = "%s"\n' "$subscription_url"
    printf 'output = "%s"\n' "$subscription_file"
    printf 'fail\nlocation\nsilent\nshow-error\n'
    printf 'connect-timeout = 10\nmax-time = 60\n'
  } | curl --config -

  "$hiddify_core" parse "$subscription_file" -o "$parsed_config"

  jq -e --arg direct_origins "${HIDDIFY_DIRECT_ORIGINS:-}" '
    ((.outbounds // []) | sort_by(if .type == "vless" then 1 else 0 end)) as $nodes
    | ($nodes | map(.tag // "")) as $tags
    | ($direct_origins | split(",")
        | map(ascii_downcase | capture("^\\s*https?://(?<host>\\[[^\\]]+\\]|[^/:?#\\s]+)").host)
        | unique) as $direct_hosts
    | ($direct_hosts | map(select(test("^[0-9.]+$")) + "/32")
        + ($direct_hosts | map(select(startswith("[")) | ltrimstr("[") | rtrimstr("]") + "/128"))) as $direct_ips
    | ($direct_hosts | map(select((test("^[0-9.]+$") or startswith("[")) | not))) as $direct_domains
    | select(($nodes | length) > 0)
    | select(all($tags[]; type == "string" and length > 0))
    | select(($tags | unique | length) == ($tags | length))
    | select(all($tags[]; . != "proxy" and . != "direct" and . != "block"))
    | {
        log: {level: "info"},
        inbounds: [
          {
            type: "mixed",
            tag: "mixed-in",
            listen: "0.0.0.0",
            listen_port: 7890
          },
          {
            type: "direct",
            tag: "domain-research-ingress",
            listen: "0.0.0.0",
            listen_port: 8003,
            network: "tcp",
            override_address: "domain-research-mcp",
            override_port: 8003
          }
        ],
        outbounds: ([{
          type: "urltest",
          tag: "proxy",
          outbounds: $tags,
          url: "https://www.gstatic.com/generate_204",
          interval: "5m",
          tolerance: 50,
          interrupt_exist_connections: true
        }] + $nodes + [
          {type: "direct", tag: "direct"},
          {type: "block", tag: "block"}
        ]),
        route: {
          rules: ([{
            inbound: ["domain-research-ingress"],
            action: "route",
            outbound: "direct"
          }]
          + (if ($direct_domains | length) > 0
             then [{domain: $direct_domains, action: "route", outbound: "direct"}] else [] end)
          + (if ($direct_ips | length) > 0
             then [{ip_cidr: $direct_ips, action: "route", outbound: "direct"}] else [] end)),
          final: "proxy"
        }
      }
  ' "$parsed_config" > "$candidate_config"
}

start_core() {
  "$hiddify_core" -D "$state_dir/core" srun -c "$active_config" &
  core_pid=$!
}

probe_core() {
  attempts=0
  while [ "$attempts" -lt 12 ]; do
    if ! kill -0 "$core_pid" 2>/dev/null; then
      return 1
    fi
    if curl --proxy http://127.0.0.1:7890 --fail --silent --show-error \
      --connect-timeout 5 --max-time 15 --output /dev/null "$probe_url"; then
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 5
  done
  return 1
}

activate_candidate() {
  if [ -f "$active_config" ]; then
    cp "$active_config" "$previous_config"
  else
    rm -f "$previous_config"
  fi
  mv "$candidate_config" "$active_config"
  stop_core
  start_core
  if probe_core; then
    return 0
  fi

  stop_core
  if [ ! -s "$previous_config" ]; then
    return 1
  fi
  mv "$previous_config" "$active_config"
  start_core
  probe_core
}

if prepare_candidate; then
  if ! activate_candidate; then
    echo "Hiddify candidate failed and no working configuration could be restored" >&2
    exit 1
  fi
elif [ -s "$active_config" ]; then
  echo "Hiddify subscription refresh failed; starting the last valid configuration" >&2
  start_core
  if ! probe_core; then
    echo "The last valid Hiddify configuration is not usable" >&2
    exit 1
  fi
else
  echo "Hiddify subscription could not produce an initial configuration" >&2
  exit 1
fi

elapsed=0
while kill -0 "$core_pid" 2>/dev/null; do
  sleep 5
  elapsed=$((elapsed + 5))
  if [ "$elapsed" -lt "$refresh_seconds" ]; then
    continue
  fi
  elapsed=0

  if ! prepare_candidate; then
    echo "Hiddify subscription refresh failed; keeping the active configuration" >&2
    continue
  fi
  if cmp -s "$candidate_config" "$active_config"; then
    continue
  fi
  if ! activate_candidate; then
    echo "Hiddify candidate and rollback configuration both failed" >&2
    exit 1
  fi
done

wait "$core_pid" 2>/dev/null || exit $?
exit 1
