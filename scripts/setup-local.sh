#!/bin/sh
# One-time native macOS setup. Everything downloaded stays in .local/.
set -eu
cd "$(dirname "$0")/.."
[ "$(uname -s)" = Darwin ] || { echo 'This setup script requires macOS.' >&2; exit 1; }

version=0.34.2
checksum=f33b2a5aa59bc6c961ed3ec23ba9dc646ca6d99ced8d2a0d46eb3a522167dd3f
base="$PWD/.local"
binary="$base/ollama/ollama"
archive="$base/ollama-darwin.tgz"
endpoint=http://127.0.0.1:11434
mkdir -p "$base/ollama"

if [ ! -x "$binary" ]; then
    echo "Downloading Ollama $version..."
    trap 'rm -f "$archive"' EXIT
    curl --fail --location --show-error \
        "https://github.com/ollama/ollama/releases/download/v$version/ollama-darwin.tgz" \
        --output "$archive"
    printf '%s  %s\n' "$checksum" "$archive" | shasum -a 256 -c -
    tar -xzf "$archive" -C "$base/ollama"
    rm "$archive"
fi

if curl --fail --silent --max-time 2 "$endpoint/api/version" >/dev/null; then
    if [ ! -f "$base/ollama.pid" ] || \
       ! lsof -nP -a -p "$(cat "$base/ollama.pid")" -iTCP:11434 -sTCP:LISTEN >/dev/null; then
        echo 'Port 11434 is used by another Ollama. Stop it, then rerun this script.' >&2
        exit 1
    fi
else
    echo 'Starting Ollama on 127.0.0.1:11434...'
    nohup env OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NO_CLOUD=1 \
        OLLAMA_MODELS="$base/models" "$binary" serve \
        >"$base/ollama.log" 2>&1 </dev/null &
    echo $! > "$base/ollama.pid"
fi

attempt=0
until curl --fail --silent --max-time 2 "$endpoint/api/version" >/dev/null; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        echo "Ollama did not start. Read $base/ollama.log" >&2
        exit 1
    fi
    sleep 1
done

OLLAMA_HOST=127.0.0.1:11434 "$binary" pull qwen2.5-coder:7b
echo 'Ready. Run: python3 src/runner.py attack'
