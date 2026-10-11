#!/usr/bin/env bash
# Remove mdv's desktop integration and restore Ghostwriter as the Markdown default.
set -euo pipefail

rm -f ~/.local/bin/mdv ~/.local/share/applications/mdv.desktop \
      ~/.local/share/icons/hicolor/scalable/apps/mdv.svg
update-desktop-database ~/.local/share/applications 2>/dev/null || true
xdg-mime default ghostwriter.desktop text/markdown text/x-markdown
echo "Uninstalled. Default for text/markdown: $(xdg-mime query default text/markdown)"
