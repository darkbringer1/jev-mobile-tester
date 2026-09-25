#!/bin/sh
# Check the tools Jev Mobile needs; print how to install anything missing.
missing=0
ok() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad() { printf '  \033[31m✗\033[0m %s\n      → %s\n' "$1" "$2"; missing=1; }

[ "$(uname -s)" = Darwin ] && ok "macOS" || bad "macOS required" "Jev Mobile targets the iOS simulator"
if [ "$(uname -m)" = arm64 ]; then ok "Apple Silicon (local Laya model supported)"
else printf '  \033[33m!\033[0m Intel Mac: skip `make laya`; use the hosted Jev backend\n'; fi
command -v uv >/dev/null && ok "uv $(uv --version | cut -d' ' -f2)" \
  || bad "uv not found" "curl -LsSf https://astral.sh/uv/install.sh | sh"
xcrun simctl help >/dev/null 2>&1 && ok "Xcode simulator tools" \
  || bad "Xcode simulator tools not found" "install Xcode, then: sudo xcode-select -s /Applications/Xcode.app"
java -version >/dev/null 2>&1 && ok "Java ($(java -version 2>&1 | head -1 | cut -d'"' -f2))" \
  || bad "Java not found (Maestro needs Java 17+)" "brew install openjdk@17"
command -v maestro >/dev/null && ok "Maestro $(maestro --version 2>/dev/null | tail -1)" \
  || bad "Maestro not found" "curl -fsSL https://get.maestro.mobile.dev | bash"
exit $missing
