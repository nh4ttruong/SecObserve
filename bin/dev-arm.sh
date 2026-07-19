#!/bin/sh

set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
override_file=$(mktemp -t secobserve-arm)

cleanup() {
  rm -f "$override_file"
}
trap cleanup EXIT INT TERM

cat >"$override_file" <<'YAML'
services:
  frontend:
    volumes:
      - dev_node_modules:/app/node_modules
    command: sh -c "npm install --no-audit --no-fund && npm run start -- --host"

volumes:
  dev_node_modules:
YAML

cd "$repo_dir"

# Always use the native architecture for this ARM-specific launcher. In particular,
# do not inherit linux/amd64 from the shell: Compose would then reject locally cached
# ARM64 images (such as postgres) before it can start the services.
export DOCKER_DEFAULT_PLATFORM=linux/arm64/v8

docker compose \
  -f docker-compose-dev.yml \
  -f "$override_file" \
  up --build
