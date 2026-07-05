#!/bin/sh
cd "$(dirname "$0")" || exit 1
echo "Metsatoo kontrolli app kaivitub..."
echo "Ava brauseris: http://127.0.0.1:8000"
echo "Ara sulge seda terminali, muidu programm peatub."
if command -v python3 >/dev/null 2>&1; then
  python3 app.py
else
  python app.py
fi
