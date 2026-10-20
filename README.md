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

Google OAuth files (`credentials.json` and `token.json`) belong in `backend/`. Enable the
Google Drive, Google Docs, Google Sheets, Google Slides, Google Calendar, and Google
Photos Picker APIs in the Google Cloud project associated with `credentials.json`. To
configure Google OAuth, run
`.venv\Scripts\python.exe backend\configurar_google.py` from the repository root. The
Drive OAuth scope is broad: it authorizes Jarvis to access files in the connected
account, and the Drive tools can move items or send them to the reversible trash. Sheets
tools can create spreadsheets in a chosen Drive folder, read and update A1 ranges,
append rows, and clear ranges. Slides tools can create presentations in Drive, read slide
text, add slides, replace text, and remove slides. Both services also expose their
`batchUpdate` API requests for advanced operations such as spreadsheet tab management
and formatting or detailed slide edits. Generic Drive tools can also rename, move, and
send spreadsheet/presentation files to the trash.

Google Photos is separate from Drive. Jarvis can start a Google Photos Picker session and
read only the photos/videos the user explicitly selects through the returned link. This
requires re-running OAuth configuration after enabling the Picker API, so the new Picker
scope is added to `token.json`. Google's current Photos APIs do not grant apps
unrestricted access to a user's entire library, and the Picker does not let Jarvis
create, edit, move, or delete items in Photos. No OAuth setting can remove those API
restrictions.

The web interface requests the browser's current location once per connected Jarvis
session. With the user's authorization, the coordinates are sent to OpenStreetMap
Nominatim to identify an approximate country, state, and city. The names and coordinates
are sent through the active LiveKit session and held in backend session memory only.
The Next.js server keeps a rounded-coordinate lookup key and its place names in a
short-lived in-memory cache for up to six hours; neither is written to disk or committed.
Reverse geocoding is approximate and may be unavailable. OpenStreetMap attribution is
shown in the interface. Revoke browser location access in the browser's site settings
at any time. The reverse-geocoding endpoint can be changed with the server-only
`LOCATION_REVERSE_GEOCODING_URL` environment variable to use a compatible provider if
needed. The public Nominatim service is limited to one request per second; see its
[usage policy](https://operations.osmfoundation.org/policies/nominatim/).

During a connected session, the interface also displays the computer's CPU and memory
usage, aggregate computer network send/receive rates, Brasília local time, location-sharing
coordinates and sharing status, recent notifications, and Jarvis tool activity. When
asked for the current time or date, the agent uses a dedicated `America/Sao_Paulo`
clock tool rather than UTC or the backend machine's timezone. CPU,
memory, and network metrics come from the computer running the Python backend; location
coordinates and system metrics are sent over the active LiveKit session and are not
stored.

The teaching whiteboard is available during a voice session. For requested lessons,
Jarvis can search the web using Gemini Google Search grounding, return cited source links,
and then explain the topic while adding explanatory text, shapes, arrows, and freehand
diagrams to the board. The grounding model can be changed using the optional
`GEMINI_RESEARCH_MODEL` setting (defaults to `gemini-3.8-flash`); Google Search grounding
uses the configured Gemini API key and is subject to Google API availability and usage
limits. Each search request has a 40-second API timeout and a 45-second overall deadline;
the activity panel shows elapsed time for running tools. Users can also draw, add notes,
or upload local images. The board uses a green
chalkboard surface with a wooden frame and white or colored chalk. Board contents are
saved in that browser's local storage and synchronized from Jarvis over the active LiveKit
session. Uploaded images are resized and stored in that browser only; they are not
uploaded to the backend. Photos from the web must be public HTTPS image URLs; local or
private images can be added by the user through the board's image upload.

Local environments, dependencies, build output, application memory, logs, and secrets
are excluded from Git.
