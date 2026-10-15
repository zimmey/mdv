#!/usr/bin/env bash
# Install mdv for the current user and make it the default app for Markdown files.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

chmod +x "$here/mdv.py"
mkdir -p ~/.local/bin ~/.local/share/applications
ln -sf "$here/mdv.py" ~/.local/bin/mdv
mkdir -p ~/.local/share/icons/hicolor/scalable/apps
cp "$here/mdv.svg" ~/.local/share/icons/hicolor/scalable/apps/mdv.svg
gtk-update-icon-cache -q -t ~/.local/share/icons/hicolor 2>/dev/null || true

cat > ~/.local/share/applications/mdv.desktop <<EOF
[Desktop Entry]
Type=Application
Name=mdv
GenericName=Markdown Editor
Comment=Lightweight tabbed Markdown viewer and editor
Exec=$HOME/.local/bin/mdv %F
Icon=mdv
StartupWMClass=mdv
Terminal=false
Categories=Utility;TextTools;TextEditor;Viewer;
MimeType=text/markdown;text/x-markdown;
Actions=new-window;

[Desktop Action new-window]
Name=New Window
Exec=$HOME/.local/bin/mdv --new-window
EOF

update-desktop-database ~/.local/share/applications 2>/dev/null || true
xdg-mime default mdv.desktop text/markdown text/x-markdown
echo "Installed. Default for text/markdown: $(xdg-mime query default text/markdown)"
