#!/bin/sh
# Add Leaflet to the app menu for the current user.  ./install.sh --uninstall removes it.
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

if [ "$1" = "--uninstall" ]; then
    rm -f "$APPS/leaflet.desktop" "$ICONS/leaflet.svg"
    update-desktop-database "$APPS" 2>/dev/null || true
    echo "Leaflet removed from the app menu."
    exit 0
fi

mkdir -p "$APPS" "$ICONS"
cp "$DIR/icons/hicolor/scalable/apps/leaflet.svg" "$ICONS/leaflet.svg"
chmod +x "$DIR/leaflet.py"

cat > "$APPS/leaflet.desktop" <<EOD
[Desktop Entry]
Type=Application
Name=Leaflet
GenericName=Document Viewer and Editor
Comment=Read and fill in PDFs, edit Word and OpenDocument files, privately
Exec=python3 "$DIR/leaflet.py" %F
Icon=leaflet
Terminal=false
Categories=Office;Viewer;WordProcessor;
MimeType=application/pdf;application/vnd.openxmlformats-officedocument.wordprocessingml.document;application/vnd.oasis.opendocument.text;application/rtf;text/rtf;
StartupNotify=true
EOD

update-desktop-database "$APPS" 2>/dev/null || true
echo "Leaflet added to the app menu."
