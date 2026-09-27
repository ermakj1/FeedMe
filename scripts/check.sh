#!/bin/bash
# Fast syntax checks — run before pushing (also runs in GitHub Actions).
#   - every tracked .py file compiles
#   - the inline <script> in ui_manage.html parses (a JS syntax error there
#     silently breaks the whole Director UI, including the live log)
set -e
cd "$(dirname "$0")/.."

echo "Python syntax..."
git ls-files '*.py' | xargs python3 -m py_compile

if python3 -m pyflakes --version >/dev/null 2>&1; then
  echo "Undefined names (pyflakes)..."
  undefined="$(git ls-files '*.py' | xargs python3 -m pyflakes 2>&1 | grep "undefined name" || true)"
  if [ -n "$undefined" ]; then echo "$undefined"; exit 1; fi
else
  echo "(pyflakes not installed — skipping undefined-name check)"
fi

echo "Director UI JavaScript syntax..."
tmp="$(mktemp -t ui_manage.XXXXXX).js"
trap 'rm -f "$tmp"' EXIT
awk '/<script>/{f=1;next} /<\/script>/{f=0} f' ui_manage.html > "$tmp"
node --check "$tmp"

echo "All checks passed"
