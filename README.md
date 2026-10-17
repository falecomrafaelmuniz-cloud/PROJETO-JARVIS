# Jarvis

Jarvis is split into two parts in this repository:

- `backend/`: Python voice agent and local tools.
- `frontend/`: Next.js web interface.

## Run on Windows

1. If they do not already exist, copy `.env.example` to `.env` and
   `frontend/.env.example` to `frontend/.env.local`. Add your private LiveKit and Gemini
   values to `.env`; do not commit real keys.
2. Install the Python requirements into the root `.venv` environment:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
   ```

3. If frontend dependencies are not installed, run `pnpm install` from `frontend/`.
4. Run `.\start_jarvis.ps1` from the repository root to start both parts, or use
   `iniciar.bat` and `frontend/front_end.bat` separately.

Google Drive and Calendar OAuth files (`credentials.json` and `token.json`) belong in
`backend/`. To configure Google OAuth, run
`.venv\Scripts\python.exe backend\configurar_google.py` from the repository root.

The web interface requests the browser's current location once per connected Jarvis
session. The browser may remember its permission choice. Coordinates are sent through
the active LiveKit session and held in backend session memory only; they are not saved
to disk or committed. Revoke location access in the browser's site settings at any time.

Local environments, dependencies, build output, application memory, logs, and secrets
are excluded from Git.
