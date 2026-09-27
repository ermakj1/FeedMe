#!/bin/bash
# Pull latest code and rebuild the Director container.
# Run this on the Pi after changes are pushed from the Mac.

cd "$(dirname "$0")/.." || exit 1

before="$(git rev-parse HEAD)"
git pull || { echo "✗ git pull failed — not deploying"; exit 1; }
after="$(git rev-parse HEAD)"

if [ "$before" = "$after" ]; then
  echo "⚠  No new commits pulled — did you push from the Mac? Rebuilding anyway."
else
  echo "Pulled $(git rev-list --count "$before..$after") new commit(s)."
fi
echo "Deploying: $(git log -1 --format='%h %s')"

mkdir -p data   # persistent caches (mounted into the director container)

# Rebuild and restart the director; bring up homeassistant if not already running.
docker compose build director \
  && docker compose up -d --no-build homeassistant \
  && docker compose up -d director \
  && echo "✓ Director running $(git log -1 --format='%h')"
