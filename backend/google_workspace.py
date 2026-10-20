import base64
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload
from requests.exceptions import RequestException


BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"
CORE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/documents",
]
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
PHOTOS_PICKER_SCOPE = "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"
SCOPES = [
    *CORE_SCOPES,
    GMAIL_SCOPE,
    PHOTOS_PICKER_SCOPE,
]
PHOTOS_PICKER_API_BASE = "https://photospicker.googleapis.com/v1"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDES_MIME_TYPE = "application/vnd.google-apps.presentation"
MAX_TEXT_BYTES = 1_000_000
MAX_RETURNED_TEXT = 20_000


class GoogleWorkspaceError(RuntimeError):
    pass


class GoogleWorkspace:
    def __init__(self):
        self._services = {}
        self._lock = threading.RLock()
        self._memory_archive_lock = threading.Lock()

    def _run_interactive_oauth(self, scopes_to_request):
        if not CREDENTIALS_PATH.is_file():
            raise GoogleWorkspaceError(
                "OAuth ainda não foi configurado. Coloque credentials.json na pasta "
                "backend e execute: python backend/configurar_google.py"
            )
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow

            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_PATH),
                scopes_to_request,
            )
            credentials = flow.run_local_server(
                host="localhost",
                port=0,
                access_type="offline",
                prompt="consent",
            )
            TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
            self._services.clear()
            return credentials
        except Exception as error:
            raise GoogleWorkspaceError(
                f"Não foi possível concluir a autorização Google no navegador: {error}"
            ) from error

    def _credentials(self, required_scopes=None):
        with self._lock:
            scopes_needed = list(required_scopes or CORE_SCOPES)
            if not TOKEN_PATH.is_file():
                return self._run_interactive_oauth([*CORE_SCOPES, GMAIL_SCOPE])

            try:
                credentials = Credentials.from_authorized_user_file(
                    str(TOKEN_PATH),
                )
            except (OSError, ValueError, GoogleAuthError) as error:
                raise GoogleWorkspaceError(
                    f"Não foi possível ler token.json; execute python backend/configurar_google.py novamente: {error}"
                ) from error

            if credentials.scopes and not credentials.has_scopes(scopes_needed):
                merged_scopes = list(dict.fromkeys([*(credentials.scopes or []), *CORE_SCOPES, *scopes_needed]))
                credentials = self._run_interactive_oauth(merged_scopes)

            if credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                    TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
                    self._services.clear()
                except (GoogleAuthError, OSError) as error:
                    raise GoogleWorkspaceError(
                        f"Não foi possível renovar a autorização Google: {error}"
                    ) from error

            if not credentials.valid:
                raise GoogleWorkspaceError(
                    "A autorização Google expirou. Execute python backend/configurar_google.py novamente."
                )
            return credentials

    def _service(self, api_name, version, required_scopes=None):
        with self._lock:
            credentials = self._credentials(required_scopes=required_scopes)
            if api_name not in self._services:
                self._services[api_name] = build(
                    api_name,
                    version,
                    credentials=credentials,
                    cache_discovery=False,
                )
            return self._services[api_name]

    def _photos_picker_request(self, method, path, body=None, params=None):
        session = AuthorizedSession(
            self._credentials(required_scopes=[PHOTOS_PICKER_SCOPE])
        )
        try:
            response = session.request(
                method,
                f"{PHOTOS_PICKER_API_BASE}/{path}",
                json=body,
                params=params,
                timeout=30,
            )
        except RequestException as error:
            raise GoogleWorkspaceError(
                f"Não foi possível acessar o Google Photos Picker: {error}"
            ) from error
        try:
            payload = response.json()
        except ValueError as error:
            raise GoogleWorkspaceError(
                "O Google Photos Picker retornou uma resposta inválida."
            ) from error
        if not response.ok:
            message = payload.get("error", {}).get("message", response.text)
            raise GoogleWorkspaceError(
                f"Google Photos Picker retornou HTTP {response.status_code}: {message}"
            )
        return payload

    def create_photos_picker_session(self):
        session = self._photos_picker_request("POST", "sessions", body={})
        return {
            "id_sessao": session.get("id"),
            "url_selecao": session.get("pickerUri"),
            "expira_em": session.get("expireTime"),
            "instrucao": (
                "Abra o link, selecione as fotos ou vídeos desejados e conclua a "
                "seleção. Depois, use o ID da sessão para consultar os itens."
            ),
        }

    def list_photos_picker_items(self, session_id):
        if not session_id.strip():
            raise GoogleWorkspaceError("Informe o ID da sessão do Google Photos Picker.")
        session_id = session_id.strip()
        session = self._photos_picker_request(
            "GET",
            f"sessions/{quote(session_id, safe='')}",
        )
        if not session.get("mediaItemsSet"):
            return {
                "id_sessao": session_id,
                "selecao_concluida": False,
                "itens": [],
                "mensagem": "A seleção ainda não foi concluída no Google Photos.",
            }

        items = []
        page_token = None
        while True:
            params = {"sessionId": session_id}
            if page_token:
                params["pageToken"] = page_token
            page = self._photos_picker_request(
                "GET",
                "mediaItems",
                params=params,
            )
            for item in page.get("mediaItems", []):
                media_file = item.get("mediaFile", {})
                items.append(
                    {
                        "id": item.get("id"),
                        "tipo": item.get("type"),
                        "nome_arquivo": media_file.get("filename"),
                        "mime_type": media_file.get("mimeType"),
                        "url_temporaria": media_file.get("baseUrl"),
                    }
                )
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        return {
            "id_sessao": session_id,
            "selecao_concluida": True,
            "itens": items,
        }

    @staticmethod
    def _execute(request):
        try:
            return request.execute()
        except HttpError as error:
            status = getattr(error.resp, "status", "desconhecido")
            raise GoogleWorkspaceError(
                f"Google API retornou HTTP {status}: {error}"
            ) from error

    @staticmethod
    def _escape_drive_query(value):
        return value.replace("\\", "\\\\").replace("'", "\\'")

    @staticmethod
    def _file_summary(item):
        return {
            "id": item.get("id"),
            "nome": item.get("name"),
            "tipo": item.get("mimeType"),
            "link": item.get("webViewLink"),
            "modificado": item.get("modifiedTime"),
            "pastas": item.get("parents", []),
        }

    def search_drive(self, query):
        query = query.strip()
        if not query:
            raise GoogleWorkspaceError("Informe um nome ou termo para buscar no Drive.")
        escaped = self._escape_drive_query(query)
        response = self._execute(
            self._service("drive", "v3")
            .files()
            .list(
                q=(
                    "trashed = false and "
                    f"(name contains '{escaped}' or fullText contains '{escaped}')"
                ),
                pageSize=20,
                fields="files(id,name,mimeType,webViewLink,modifiedTime,parents)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
        )
        return [self._file_summary(item) for item in response.get("files", [])]

    def _resolve_folder_id(self, name_or_id):
        drive = self._service("drive", "v3")
        if not name_or_id.strip():
            raise GoogleWorkspaceError("Informe o ID ou o nome da pasta.")
        try:
            item = self._execute(
                drive.files()
                .get(
                    fileId=name_or_id,
                    fields="id,name,mimeType,trashed",
                    supportsAllDrives=True,
                )
            )
        except GoogleWorkspaceError as error:
            if "HTTP 404" not in str(error):
                raise
        else:
            if item.get("mimeType") != FOLDER_MIME_TYPE or item.get("trashed"):
                raise GoogleWorkspaceError("O ID informado não é de uma pasta ativa.")
            return item["id"]

        escaped = self._escape_drive_query(name_or_id.strip())
        response = self._execute(
            drive.files()
            .list(
                q=(
                    "trashed = false and "
                    f"mimeType = '{FOLDER_MIME_TYPE}' and name = '{escaped}'"
                ),
                pageSize=10,
                fields="files(id,name)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
        )
        matches = response.get("files", [])
        if not matches:
            raise GoogleWorkspaceError(f"Nenhuma pasta chamada '{name_or_id}' foi encontrada.")
        if len(matches) > 1:
            choices = ", ".join(f"{item['name']} (ID {item['id']})" for item in matches)
            raise GoogleWorkspaceError(
                f"Há mais de uma pasta com esse nome; informe o ID desejado: {choices}"
            )
        return matches[0]["id"]

    def list_drive_folder(self, name_or_id):
        folder_id = self._resolve_folder_id(name_or_id)
        response = self._execute(
            self._service("drive", "v3")
            .files()
            .list(
                q=f"trashed = false and '{folder_id}' in parents",
                pageSize=100,
                orderBy="name",
                fields="files(id,name,mimeType,webViewLink,modifiedTime,parents)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
        )
        return [self._file_summary(item) for item in response.get("files", [])]

    def create_drive_folder(self, name, parent_id=""):
        if not name.strip():
            raise GoogleWorkspaceError("Informe o nome da nova pasta.")
        metadata = {"name": name.strip(), "mimeType": FOLDER_MIME_TYPE}
        if parent_id.strip():
            metadata["parents"] = [self._resolve_folder_id(parent_id)]
        item = self._execute(
            self._service("drive", "v3")
            .files()
            .create(
                body=metadata,
                fields="id,name,mimeType,webViewLink",
                supportsAllDrives=True,
            )
        )
        return self._file_summary(item)

    def create_text_file(self, name, content, parent_id=""):
        if not name.strip():
            raise GoogleWorkspaceError("Informe o nome do arquivo.")
        metadata = {"name": name.strip(), "mimeType": "text/plain"}
        if parent_id.strip():
            metadata["parents"] = [self._resolve_folder_id(parent_id)]
        media = MediaInMemoryUpload(
            content.encode("utf-8"),
            mimetype="text/plain",
            resumable=False,
        )
        item = self._execute(
            self._service("drive", "v3")
            .files()
            .create(
                body=metadata,
                media_body=media,
                fields="id,name,mimeType,webViewLink",
                supportsAllDrives=True,
            )
        )
        return self._file_summary(item)

    def update_text_file(self, file_id, content):
        if not file_id.strip():
            raise GoogleWorkspaceError("Informe o ID do arquivo de texto.")

        drive = self._service("drive", "v3")
        metadata = self._execute(
            drive.files()
            .get(
                fileId=file_id,
                fields="id,name,mimeType,trashed",
                supportsAllDrives=True,
            )
        )
        if metadata.get("trashed"):
            raise GoogleWorkspaceError("O arquivo está na lixeira.")
        if metadata.get("mimeType") != "text/plain":
            raise GoogleWorkspaceError(
                "Este arquivo não é um arquivo de texto simples (text/plain)."
            )
        if metadata.get("name") == "Memorias_do_Jarvis.txt":
            raise GoogleWorkspaceError(
                "O arquivo central de memórias só pode ser atualizado pela ferramenta "
                "salvar_na_memoria, que acrescenta uma entrada numerada sem substituir "
                "o histórico existente."
            )

        media = MediaInMemoryUpload(
            content.encode("utf-8"),
            mimetype="text/plain",
            resumable=False,
        )
        item = self._execute(
            drive.files()
            .update(
                fileId=file_id,
                media_body=media,
                fields="id,name,mimeType,webViewLink",
                supportsAllDrives=True,
            )
        )
        return self._file_summary(item)

    def archive_jarvis_memory(self, content, memory_id=""):
        if not content.strip():
            raise GoogleWorkspaceError("A lembrança não pode ficar vazia.")

        with self._memory_archive_lock:
            drive = self._service("drive", "v3")
            escaped_name = self._escape_drive_query("Memórias do Jarvis")
            response = self._execute(
                drive.files()
                .list(
                    q=(
                        "trashed = false and "
                        f"mimeType = '{FOLDER_MIME_TYPE}' and "
                        f"name = '{escaped_name}' and 'root' in parents"
                    ),
                    pageSize=10,
                    fields="files(id,name)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
            )
            folders = response.get("files", [])
            if len(folders) > 1:
                raise GoogleWorkspaceError(
                    "Há mais de uma pasta 'Memórias do Jarvis' na raiz do Drive."
                )
            if folders:
                folder_id = folders[0]["id"]
            else:
                folder = self.create_drive_folder("Memórias do Jarvis")
                folder_id = folder["id"]

            existing_files = []
            page_token = None
            while True:
                parameters = {
                    "q": f"trashed = false and '{folder_id}' in parents",
                    "pageSize": 100,
                    "fields": "nextPageToken,files(id,name,mimeType)",
                    "supportsAllDrives": True,
                    "includeItemsFromAllDrives": True,
                }
                if page_token:
                    parameters["pageToken"] = page_token
                page = self._execute(drive.files().list(**parameters))
                existing_files.extend(page.get("files", []))
                page_token = page.get("nextPageToken")
                if not page_token:
                    break

            archive_name = "Memorias_do_Jarvis.txt"
            archives = [
                item for item in existing_files
                if item.get("name") == archive_name
            ]
            if len(archives) > 1:
                raise GoogleWorkspaceError(
                    "Há mais de um arquivo 'Memorias_do_Jarvis.txt' na pasta de memórias."
                )
            other_text_files = [
                item
                for item in existing_files
                if item.get("mimeType") == "text/plain"
                and item.get("name") != archive_name
            ]
            if other_text_files:
                names = ", ".join(item.get("name", "sem nome") for item in other_text_files)
                raise GoogleWorkspaceError(
                    "A pasta 'Memórias do Jarvis' deve manter um único arquivo de texto "
                    f"para o histórico. Encontrei outros arquivos de texto: {names}. "
                    "Nenhum arquivo foi alterado."
                )

            archive_content = ""
            if archives:
                response = self._execute(
                    drive.files().get_media(fileId=archives[0]["id"])
                )
                archive_content = (
                    response if isinstance(response, str) else response.decode("utf-8")
                )

            existing_numbers = [
                int(match.group(1))
                for item in existing_files
                if (match := re.fullmatch(
                    r"Memoria_(\d+)\.txt",
                    item.get("name", ""),
                ))
            ]
            existing_numbers.extend(
                int(number)
                for number in re.findall(
                    r"(?m)^Mem[oó]ria\s+(\d+)\b",
                    archive_content,
                )
            )
            memory_number = max(existing_numbers, default=0) + 1
            saved_at = datetime.now(timezone.utc).isoformat()
            new_entry = (
                f"Memória {memory_number:04d}\n"
                f"Data de gravação (UTC): {saved_at}\n"
                f"ID local: {memory_id or 'não informado'}\n\n"
                f"{content.strip()}\n"
            )
            updated_content = (
                f"{archive_content.rstrip()}\n\n{new_entry}"
                if archive_content.strip()
                else new_entry
            )

            if archives:
                media = MediaInMemoryUpload(
                    updated_content.encode("utf-8"),
                    mimetype="text/plain",
                    resumable=False,
                )
                item = self._execute(
                    drive.files()
                    .update(
                        fileId=archives[0]["id"],
                        media_body=media,
                        fields="id,name,mimeType,webViewLink",
                        supportsAllDrives=True,
                    )
                )
                item = self._file_summary(item)
            else:
                item = self.create_text_file(
                    archive_name,
                    updated_content,
                    folder_id,
                )
            return {
                **item,
                "numero_memoria": memory_number,
            }

    def create_google_doc(self, name, content="", parent_id=""):
        if not name.strip():
            raise GoogleWorkspaceError("Informe o nome do documento.")
        metadata = {"name": name.strip(), "mimeType": GOOGLE_DOC_MIME_TYPE}
        if parent_id.strip():
            metadata["parents"] = [self._resolve_folder_id(parent_id)]
        item = self._execute(
            self._service("drive", "v3")
            .files()
            .create(
                body=metadata,
                fields="id,name,mimeType,webViewLink",
                supportsAllDrives=True,
            )
        )
        if content:
            try:
                self._execute(
                    self._service("docs", "v1")
                    .documents()
                    .batchUpdate(
                        documentId=item["id"],
                        body={
                            "requests": [
                                {
                                    "insertText": {
                                        "location": {"index": 1},
                                        "text": content,
                                    }
                                }
                            ]
                        },
                    )
                )
            except GoogleWorkspaceError as error:
                raise GoogleWorkspaceError(
                    f"O documento foi criado (ID {item['id']}), mas não foi possível inserir o texto: {error}"
                ) from error
        return self._file_summary(item)

    @staticmethod
    def _parse_sheet_values(values_json):
        try:
            values = json.loads(values_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise GoogleWorkspaceError(
                "Os dados devem ser um JSON válido no formato de matriz de linhas."
            ) from error
        if not isinstance(values, list) or any(
            not isinstance(row, list) for row in values
        ):
            raise GoogleWorkspaceError(
                "Os dados devem estar no formato [[\"coluna 1\", \"coluna 2\"], ...]."
            )
        if any(
            value is not None and not isinstance(value, (str, int, float, bool))
            for row in values
            for value in row
        ):
            raise GoogleWorkspaceError(
                "Cada célula deve conter texto, número, booleano ou null."
            )
        return values

    @staticmethod
    def _parse_api_requests(requests_json):
        try:
            requests = json.loads(requests_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise GoogleWorkspaceError(
                "As operações devem ser um JSON válido no formato de lista."
            ) from error
        if not isinstance(requests, list) or any(
            not isinstance(request, dict) or len(request) != 1
            for request in requests
        ):
            raise GoogleWorkspaceError(
                "Informe uma lista JSON de operações da API, cada uma com um único tipo."
            )
        return requests

    def create_google_sheet(self, name, values_json="[]", parent_id=""):
        if not name.strip():
            raise GoogleWorkspaceError("Informe o nome da planilha.")
        values = self._parse_sheet_values(values_json)
        sheets = self._service("sheets", "v4")
        spreadsheet = self._execute(
            sheets.spreadsheets().create(
                body={"properties": {"title": name.strip()}},
                fields="spreadsheetId,spreadsheetUrl,properties.title",
            )
        )
        spreadsheet_id = spreadsheet["spreadsheetId"]
        try:
            if values:
                self._execute(
                    sheets.spreadsheets()
                    .values()
                    .update(
                        spreadsheetId=spreadsheet_id,
                        range="A1",
                        valueInputOption="USER_ENTERED",
                        body={"values": values},
                    )
                )
            if parent_id.strip():
                self.move_drive_item(spreadsheet_id, parent_id)
        except GoogleWorkspaceError as error:
            raise GoogleWorkspaceError(
                f"A planilha foi criada (ID {spreadsheet_id}), mas não foi possível "
                f"completar a operação: {error}"
            ) from error
        return {
            "id": spreadsheet_id,
            "nome": name.strip(),
            "link": spreadsheet.get("spreadsheetUrl"),
        }

    def _resolve_spreadsheet_id(self, spreadsheet_id_or_name):
        identifier = (spreadsheet_id_or_name or "").strip()
        if not identifier:
            raise GoogleWorkspaceError("Informe o nome ou ID da planilha.")
        # Se tem cara de ID direto do Google Sheets (sem espaços, mais de 25 caracteres)
        if len(identifier) >= 28 and " " not in identifier and not identifier.endswith((".xlsx", ".csv", ".tsv")):
            return identifier

        termo_limpo = identifier.replace("'", "\\'")
        drive = self._service("drive", "v3")
        try:
            res = self._execute(
                drive.files().list(
                    q=f"mimeType = '{GOOGLE_SHEET_MIME_TYPE}' and name contains '{termo_limpo}' and trashed = false",
                    fields="files(id, name)",
                    pageSize=5,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
            )
            files = res.get("files", [])
        except Exception:
            files = []

        if not files:
            try:
                res_fb = self._execute(
                    drive.files().list(
                        q=f"name contains '{termo_limpo}' and trashed = false",
                        fields="files(id, name, mimeType)",
                        pageSize=5,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                    )
                )
                files = [f for f in res_fb.get("files", []) if f.get("mimeType") == GOOGLE_SHEET_MIME_TYPE] or res_fb.get("files", [])
            except Exception:
                files = []

        if not files:
            raise GoogleWorkspaceError(f"Não encontrei nenhuma Planilha Google com o nome '{identifier}'.")

        for f in files:
            if f.get("name", "").lower() == identifier.lower():
                return f["id"]
        return files[0]["id"]

    def read_google_sheet(self, spreadsheet_id, range_a1="A:Z"):
        real_id = self._resolve_spreadsheet_id(spreadsheet_id)
        result = self._execute(
            self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .get(spreadsheetId=real_id, range=range_a1)
        )
        return {
            "id": real_id,
            "intervalo": result.get("range", range_a1),
            "valores": result.get("values", []),
        }

    def update_google_sheet(self, spreadsheet_id, range_a1, values_json):
        real_id = self._resolve_spreadsheet_id(spreadsheet_id)
        if not range_a1.strip():
            raise GoogleWorkspaceError("Informe o intervalo A1 a atualizar.")
        values = self._parse_sheet_values(values_json)
        result = self._execute(
            self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .update(
                spreadsheetId=real_id,
                range=range_a1,
                valueInputOption="USER_ENTERED",
                body={"values": values},
            )
        )
        return {
            "id": real_id,
            "intervalo": result.get("updatedRange", range_a1),
            "celulas_atualizadas": result.get("updatedCells", 0),
        }

    def append_google_sheet(self, spreadsheet_id, range_a1, values_json):
        real_id = self._resolve_spreadsheet_id(spreadsheet_id)
        if not range_a1.strip():
            raise GoogleWorkspaceError("Informe o intervalo A1 da tabela.")
        values = self._parse_sheet_values(values_json)
        result = self._execute(
            self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .append(
                spreadsheetId=real_id,
                range=range_a1,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": values},
            )
        )
        updates = result.get("updates", {})
        return {
            "id": real_id,
            "intervalo": updates.get("updatedRange", ""),
            "linhas_atualizadas": updates.get("updatedRows", 0),
        }

    def clear_google_sheet(self, spreadsheet_id, range_a1):
        real_id = self._resolve_spreadsheet_id(spreadsheet_id)
        if not range_a1.strip():
            raise GoogleWorkspaceError("Informe o intervalo A1 a limpar.")
        result = self._execute(
            self._service("sheets", "v4")
            .spreadsheets()
            .values()
            .clear(
                spreadsheetId=real_id,
                range=range_a1,
                body={},
            )
        )
        return {
            "id": real_id,
            "intervalo": result.get("clearedRange", range_a1),
        }

    def batch_update_google_sheet(self, spreadsheet_id, requests_json):
        real_id = self._resolve_spreadsheet_id(spreadsheet_id)
        requests = self._parse_api_requests(requests_json)
        result = self._execute(
            self._service("sheets", "v4")
            .spreadsheets()
            .batchUpdate(
                spreadsheetId=real_id,
                body={"requests": requests},
            )
        )
        return {
            "id": real_id,
            "operacoes": len(result.get("replies", [])),
            "respostas": result.get("replies", []),
        }

    def create_google_slides(self, name, parent_id=""):
        if not name.strip():
            raise GoogleWorkspaceError("Informe o nome da apresentação.")
        presentation = self._execute(
            self._service("slides", "v1")
            .presentations()
            .create(body={"title": name.strip()})
        )
        presentation_id = presentation["presentationId"]
        if parent_id.strip():
            try:
                self.move_drive_item(presentation_id, parent_id)
            except GoogleWorkspaceError as error:
                raise GoogleWorkspaceError(
                    f"A apresentação foi criada (ID {presentation_id}), mas não foi "
                    f"possível movê-la para a pasta: {error}"
                ) from error
        return {
            "id": presentation_id,
            "nome": presentation.get("title", name.strip()),
            "link": f"https://docs.google.com/presentation/d/{presentation_id}/edit",
        }

    def read_google_slides(self, presentation_id):
        presentation = self._execute(
            self._service("slides", "v1")
            .presentations()
            .get(presentationId=presentation_id)
        )
        slides = []
        for index, slide in enumerate(presentation.get("slides", []), start=1):
            texts = []
            for element in slide.get("pageElements", []):
                shape = element.get("shape", {})
                text = shape.get("text", {})
                for text_element in text.get("textElements", []):
                    run = text_element.get("textRun", {})
                    if run.get("content"):
                        texts.append(run["content"])
            slides.append(
                {"numero": index, "id": slide.get("objectId"), "texto": "".join(texts)}
            )
        return {
            "id": presentation_id,
            "nome": presentation.get("title"),
            "slides": slides,
        }

    def add_google_slide(self, presentation_id, text):
        slide_id = f"slide_{uuid.uuid4().hex}"
        shape_id = f"text_{uuid.uuid4().hex}"
        requests = [
            {
                "createSlide": {
                    "objectId": slide_id,
                    "slideLayoutReference": {"predefinedLayout": "BLANK"},
                }
            }
        ]
        if text:
            requests.extend(
                [
                    {
                        "createShape": {
                            "objectId": shape_id,
                            "shapeType": "TEXT_BOX",
                            "elementProperties": {
                                "pageObjectId": slide_id,
                                "size": {
                                    "width": {"magnitude": 600, "unit": "PT"},
                                    "height": {"magnitude": 300, "unit": "PT"},
                                },
                                "transform": {
                                    "scaleX": 1,
                                    "scaleY": 1,
                                    "translateX": 40,
                                    "translateY": 40,
                                    "unit": "PT",
                                },
                            },
                        }
                    },
                    {
                        "insertText": {
                            "objectId": shape_id,
                            "insertionIndex": 0,
                            "text": text,
                        }
                    },
                ]
            )
        self._execute(
            self._service("slides", "v1")
            .presentations()
            .batchUpdate(
                presentationId=presentation_id,
                body={"requests": requests},
            )
        )
        return {
            "id": presentation_id,
            "slide_id": slide_id,
            "texto": text,
        }

    def replace_google_slides_text(self, presentation_id, old_text, new_text):
        if not old_text:
            raise GoogleWorkspaceError("O texto a localizar não pode ficar vazio.")
        result = self._execute(
            self._service("slides", "v1")
            .presentations()
            .batchUpdate(
                presentationId=presentation_id,
                body={
                    "requests": [
                        {
                            "replaceAllText": {
                                "containsText": {
                                    "text": old_text,
                                    "matchCase": True,
                                },
                                "replaceText": new_text,
                            }
                        }
                    ]
                },
            )
        )
        return {
            "id": presentation_id,
            "substituicoes": result["replies"][0]["replaceAllText"][
                "occurrencesChanged"
            ],
        }

    def delete_google_slide(self, presentation_id, slide_id):
        self._execute(
            self._service("slides", "v1")
            .presentations()
            .batchUpdate(
                presentationId=presentation_id,
                body={"requests": [{"deleteObject": {"objectId": slide_id}}]},
            )
        )
        return {"id": presentation_id, "slide_removido": slide_id}

    def batch_update_google_slides(self, presentation_id, requests_json):
        requests = self._parse_api_requests(requests_json)
        result = self._execute(
            self._service("slides", "v1")
            .presentations()
            .batchUpdate(
                presentationId=presentation_id,
                body={"requests": requests},
            )
        )
        return {
            "id": presentation_id,
            "operacoes": len(result.get("replies", [])),
            "respostas": result.get("replies", []),
        }

    def read_drive_file(self, file_id):
        drive = self._service("drive", "v3")
        metadata = self._execute(
            drive.files()
            .get(
                fileId=file_id,
                fields="id,name,mimeType,size,webViewLink,modifiedTime,trashed",
                supportsAllDrives=True,
            )
        )
        if metadata.get("trashed"):
            raise GoogleWorkspaceError("O arquivo está na lixeira.")

        mime_type = metadata.get("mimeType", "")
        export_types = {
            GOOGLE_DOC_MIME_TYPE: "text/plain",
            GOOGLE_SHEET_MIME_TYPE: "text/csv",
            GOOGLE_SLIDES_MIME_TYPE: "text/plain",
        }
        if mime_type in export_types:
            content = self._execute(
                drive.files().export(
                    fileId=file_id,
                    mimeType=export_types[mime_type],
                )
            )
        else:
            if not mime_type.startswith("text/") and mime_type not in {
                "application/json",
                "application/xml",
            }:
                raise GoogleWorkspaceError(
                    "Este tipo de arquivo não pode ser mostrado como texto. "
                    "A leitura nesta versão aceita Google Docs, Planilhas, Apresentações e arquivos de texto."
                )
            file_size = int(metadata.get("size", 0))
            if file_size > MAX_TEXT_BYTES:
                raise GoogleWorkspaceError(
                    f"O arquivo excede o limite de leitura de {MAX_TEXT_BYTES} bytes."
                )
            content = self._execute(drive.files().get_media(fileId=file_id))

        if isinstance(content, str):
            text = content
        else:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise GoogleWorkspaceError(
                    "O arquivo não contém texto UTF-8 legível por esta ferramenta."
                ) from error
        truncated = len(text) > MAX_RETURNED_TEXT
        return {
            "id": file_id,
            "nome": metadata.get("name"),
            "tipo": mime_type,
            "conteudo": text[:MAX_RETURNED_TEXT],
            "conteudo_truncado": truncated,
            "link": metadata.get("webViewLink"),
        }

    def rename_drive_item(self, file_id, new_name):
        if not new_name.strip():
            raise GoogleWorkspaceError("Informe o novo nome.")
        item = self._execute(
            self._service("drive", "v3")
            .files()
            .update(
                fileId=file_id,
                body={"name": new_name.strip()},
                fields="id,name,mimeType,webViewLink",
                supportsAllDrives=True,
            )
        )
        return self._file_summary(item)

    def move_drive_item(self, file_id, destination_folder_id):
        drive = self._service("drive", "v3")
        destination_id = self._resolve_folder_id(destination_folder_id)
        item = self._execute(
            drive.files()
            .get(
                fileId=file_id,
                fields="id,name,parents",
                supportsAllDrives=True,
            )
        )
        old_parents = ",".join(item.get("parents", []))
        updated = self._execute(
            drive.files()
            .update(
                fileId=file_id,
                addParents=destination_id,
                removeParents=old_parents or None,
                fields="id,name,mimeType,webViewLink,parents",
                supportsAllDrives=True,
            )
        )
        return self._file_summary(updated)

    def replace_google_doc_text(self, document_id, old_text, new_text):
        if not old_text:
            raise GoogleWorkspaceError("O texto a localizar não pode ficar vazio.")
        docs = self._service("docs", "v1")
        result = self._execute(
            docs.documents()
            .batchUpdate(
                documentId=document_id,
                body={
                    "requests": [
                        {
                            "replaceAllText": {
                                "containsText": {
                                    "text": old_text,
                                    "matchCase": True,
                                },
                                "replaceText": new_text,
                            }
                        }
                    ]
                },
            )
        )
        return {
            "document_id": document_id,
            "substituicoes": result["replies"][0]["replaceAllText"]["occurrencesChanged"],
        }

    def trash_drive_item(self, file_id):
        item = self._execute(
            self._service("drive", "v3")
            .files()
            .update(
                fileId=file_id,
                body={"trashed": True},
                fields="id,name,trashed",
                supportsAllDrives=True,
            )
        )
        return {
            "id": item.get("id"),
            "nome": item.get("name"),
            "na_lixeira": item.get("trashed", False),
        }

    @staticmethod
    def _normalize_calendar_iso(value):
        cleaned = value.strip()
        if not cleaned:
            return ""
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
            cleaned = f"{cleaned}T09:00:00-03:00"
        elif not re.search(r"(Z|[+-]\d{2}:\d{2})$", cleaned, flags=re.IGNORECASE):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", cleaned):
                cleaned = cleaned.replace(" ", "T") + ":00-03:00"
            else:
                cleaned = cleaned.replace(" ", "T") + "-03:00"
        return cleaned

    def list_calendar_events(self, time_min="", time_max=""):
        if not time_min.strip():
            time_min = datetime.now(timezone.utc).isoformat()
        else:
            time_min = self._normalize_calendar_iso(time_min)
        parameters = {
            "calendarId": "primary",
            "timeMin": time_min,
            "maxResults": 50,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if time_max.strip():
            parameters["timeMax"] = self._normalize_calendar_iso(time_max)
        response = self._execute(
            self._service("calendar", "v3")
            .events()
            .list(**parameters)
        )
        return [
            {
                "id": event.get("id"),
                "titulo": event.get("summary"),
                "inicio": event.get("start"),
                "fim": event.get("end"),
                "descricao": event.get("description"),
                "link": event.get("htmlLink"),
            }
            for event in response.get("items", [])
        ]

    def create_calendar_event(self, summary, start_iso, end_iso, description=""):
        if not summary.strip() or not start_iso.strip() or not end_iso.strip():
            raise GoogleWorkspaceError("Informe título, início e fim do evento.")
        start_norm = self._normalize_calendar_iso(start_iso)
        end_norm = self._normalize_calendar_iso(end_iso)
        event = self._execute(
            self._service("calendar", "v3")
            .events()
            .insert(
                calendarId="primary",
                body={
                    "summary": summary.strip(),
                    "description": description,
                    "start": {"dateTime": start_norm, "timeZone": "America/Sao_Paulo"},
                    "end": {"dateTime": end_norm, "timeZone": "America/Sao_Paulo"},
                },
            )
        )
        return {
            "id": event.get("id"),
            "titulo": event.get("summary"),
            "inicio": event.get("start"),
            "fim": event.get("end"),
            "link": event.get("htmlLink"),
        }

    def update_calendar_event(
        self,
        event_id,
        summary="",
        description="",
        start_iso="",
        end_iso="",
    ):
        changes = {}
        if summary.strip():
            changes["summary"] = summary.strip()
        if description:
            changes["description"] = description
        if start_iso.strip():
            changes["start"] = {
                "dateTime": self._normalize_calendar_iso(start_iso),
                "timeZone": "America/Sao_Paulo",
            }
        if end_iso.strip():
            changes["end"] = {
                "dateTime": self._normalize_calendar_iso(end_iso),
                "timeZone": "America/Sao_Paulo",
            }
        if not changes:
            raise GoogleWorkspaceError("Informe pelo menos um campo para alterar.")
        event = self._execute(
            self._service("calendar", "v3")
            .events()
            .patch(
                calendarId="primary",
                eventId=event_id,
                body=changes,
            )
        )
        return {
            "id": event.get("id"),
            "titulo": event.get("summary"),
            "inicio": event.get("start"),
            "fim": event.get("end"),
            "link": event.get("htmlLink"),
        }

    def delete_calendar_event(self, event_id):
        self._execute(
            self._service("calendar", "v3")
            .events()
            .delete(calendarId="primary", eventId=event_id)
        )
        return {"id": event_id, "excluido": True}

    def _gmail_service(self):
        return self._service("gmail", "v1", required_scopes=[GMAIL_SCOPE])

    @staticmethod
    def _extract_headers(headers_list):
        headers = {}
        for item in headers_list or []:
            name = (item.get("name") or "").lower()
            if name in {"subject", "from", "to", "date", "cc", "message-id", "references"}:
                headers[name] = item.get("value", "")
        return headers

    @classmethod
    def _extract_email_body(cls, payload):
        if not payload:
            return ""
        mime_type = payload.get("mimeType", "")
        body_data = (payload.get("body") or {}).get("data")
        if body_data and mime_type == "text/plain":
            try:
                return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
            except Exception:
                pass

        parts = payload.get("parts") or []
        plain_parts = []
        html_parts = []
        for part in parts:
            part_mime = part.get("mimeType", "")
            part_data = (part.get("body") or {}).get("data")
            if part_data and part_mime == "text/plain":
                try:
                    plain_parts.append(
                        base64.urlsafe_b64decode(part_data).decode("utf-8", errors="replace")
                    )
                except Exception:
                    pass
            elif part_data and part_mime == "text/html":
                try:
                    raw_html = base64.urlsafe_b64decode(part_data).decode("utf-8", errors="replace")
                    cleaned = re.sub(r"<style[^>]*>.*?</style>", " ", raw_html, flags=re.S | re.I)
                    cleaned = re.sub(r"<script[^>]*>.*?</script>", " ", cleaned, flags=re.S | re.I)
                    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
                    cleaned = re.sub(r"\s+", " ", cleaned).strip()
                    html_parts.append(cleaned)
                except Exception:
                    pass
            elif part.get("parts"):
                nested = cls._extract_email_body(part)
                if nested:
                    plain_parts.append(nested)

        if plain_parts:
            return "\n".join(plain_parts).strip()
        if html_parts:
            return "\n".join(html_parts).strip()
        if body_data:
            try:
                raw = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
                return re.sub(r"<[^>]+>", " ", raw).strip()
            except Exception:
                return ""
        return ""

    def list_gmail_messages(self, query="in:inbox", max_results=10):
        q = query.strip() if query and query.strip() else "in:inbox"
        limit = max(1, min(25, int(max_results or 10)))
        gmail = self._gmail_service()
        response = self._execute(
            gmail.users()
            .messages()
            .list(userId="me", q=q, maxResults=limit)
        )
        messages = response.get("messages", [])
        results = []
        for msg_ref in messages:
            msg_id = msg_ref.get("id")
            if not msg_id:
                continue
            detail = self._execute(
                gmail.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=["Subject", "From", "To", "Date"],
                )
            )
            headers = self._extract_headers((detail.get("payload") or {}).get("headers"))
            label_ids = detail.get("labelIds") or []
            results.append(
                {
                    "id": msg_id,
                    "thread_id": detail.get("threadId"),
                    "assunto": headers.get("subject", "(Sem assunto)"),
                    "de": headers.get("from", ""),
                    "para": headers.get("to", ""),
                    "data": headers.get("date", ""),
                    "nao_lido": "UNREAD" in label_ids,
                    "resumo": detail.get("snippet", ""),
                }
            )
        return results

    def read_gmail_message(self, message_id):
        msg_id = message_id.strip()
        if not msg_id:
            raise GoogleWorkspaceError("Informe o ID do e-mail para leitura.")
        gmail = self._gmail_service()
        detail = self._execute(
            gmail.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
        )
        payload = detail.get("payload") or {}
        headers = self._extract_headers(payload.get("headers"))
        body_text = self._extract_email_body(payload)
        label_ids = detail.get("labelIds") or []
        return {
            "id": msg_id,
            "thread_id": detail.get("threadId"),
            "assunto": headers.get("subject", "(Sem assunto)"),
            "de": headers.get("from", ""),
            "para": headers.get("to", ""),
            "cc": headers.get("cc", ""),
            "data": headers.get("date", ""),
            "nao_lido": "UNREAD" in label_ids,
            "conteudo": (body_text or detail.get("snippet", ""))[:MAX_RETURNED_TEXT],
        }

    def _attach_items_to_email(self, msg: EmailMessage, attachments):
        import mimetypes

        attached_names = []
        for item in attachments or []:
            if not isinstance(item, dict):
                continue
            item_type = item.get("tipo") or item.get("type") or "local"
            target = (item.get("caminho_ou_id") or item.get("path") or item.get("id") or "").strip()
            custom_name = (item.get("nome") or item.get("name") or "").strip()
            if not target:
                continue

            if item_type == "drive":
                drive = self._service("drive", "v3")
                meta = self._execute(
                    drive.files()
                    .get(
                        fileId=target,
                        fields="id,name,mimeType",
                        supportsAllDrives=True,
                    )
                )
                drive_name = custom_name or meta.get("name") or f"documento_{target}"
                mime_type = meta.get("mimeType") or "application/octet-stream"
                if mime_type in {
                    GOOGLE_DOC_MIME_TYPE,
                    GOOGLE_SHEET_MIME_TYPE,
                    GOOGLE_SLIDES_MIME_TYPE,
                }:
                    raw_data = self._execute(
                        drive.files().export(fileId=target, mimeType="application/pdf")
                    )
                    if not drive_name.lower().endswith(".pdf"):
                        drive_name = f"{drive_name}.pdf"
                    maintype, subtype = "application", "pdf"
                else:
                    raw_data = self._execute(drive.files().get_media(fileId=target))
                    if "/" in mime_type:
                        maintype, subtype = mime_type.split("/", 1)
                    else:
                        maintype, subtype = "application", "octet-stream"

                if isinstance(raw_data, str):
                    raw_data = raw_data.encode("utf-8")
                msg.add_attachment(
                    raw_data,
                    maintype=maintype,
                    subtype=subtype,
                    filename=drive_name,
                )
                attached_names.append(drive_name)
            else:
                file_path = Path(target)
                if not file_path.is_file():
                    raise GoogleWorkspaceError(f"Arquivo local não encontrado para anexar: {target}")
                filename = custom_name or file_path.name
                guessed_mime, _ = mimetypes.guess_type(filename)
                mime_type = guessed_mime or "application/octet-stream"
                maintype, subtype = (
                    mime_type.split("/", 1) if "/" in mime_type else ("application", "octet-stream")
                )
                msg.add_attachment(
                    file_path.read_bytes(),
                    maintype=maintype,
                    subtype=subtype,
                    filename=filename,
                )
                attached_names.append(filename)
        return attached_names

    def send_gmail_message(self, to, subject, body_text, cc="", attachments=None):
        destinatario = to.strip()
        assunto = subject.strip()
        if not destinatario or not assunto:
            raise GoogleWorkspaceError("Informe o destinatário (para) e o assunto do e-mail.")
        msg = EmailMessage()
        msg["To"] = destinatario
        msg["Subject"] = assunto
        if cc and cc.strip():
            msg["Cc"] = cc.strip()
        msg.set_content(body_text or "")

        attached_names = self._attach_items_to_email(msg, attachments)

        raw_bytes = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        sent = self._execute(
            self._gmail_service()
            .users()
            .messages()
            .send(userId="me", body={"raw": raw_bytes})
        )
        return {
            "id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "para": destinatario,
            "assunto": assunto,
            "anexos_enviados": attached_names,
            "enviado": True,
        }

    def search_gmail_contacts(self, query):
        termo = (query or "").strip()
        if not termo:
            return []
        messages = self.list_gmail_messages(query=termo, max_results=12)
        seen = set()
        contacts = []
        for item in messages:
            for header_val in (item.get("de", ""), item.get("para", "")):
                if not header_val:
                    continue
                for part in header_val.split(","):
                    cleaned = part.strip()
                    if not cleaned or cleaned.lower() in seen:
                        continue
                    if termo.lower() in cleaned.lower():
                        seen.add(cleaned.lower())
                        contacts.append(cleaned)
        return contacts[:10]

    def reply_gmail_message(self, message_id, body_text):
        msg_id = message_id.strip()
        if not msg_id or not body_text.strip():
            raise GoogleWorkspaceError("Informe o ID do e-mail original e o texto da resposta.")
        gmail = self._gmail_service()
        original = self._execute(
            gmail.users()
            .messages()
            .get(
                userId="me",
                id=msg_id,
                format="metadata",
                metadataHeaders=["Subject", "From", "Message-ID", "References"],
            )
        )
        headers = self._extract_headers((original.get("payload") or {}).get("headers"))
        reply_to = headers.get("from", "")
        if not reply_to:
            raise GoogleWorkspaceError("Não foi possível identificar o remetente do e-mail original.")
        subject = headers.get("subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        msg = EmailMessage()
        msg["To"] = reply_to
        msg["Subject"] = subject
        if headers.get("message-id"):
            msg["In-Reply-To"] = headers["message-id"]
            refs = headers.get("references", "")
            msg["References"] = f"{refs} {headers['message-id']}".strip()
        msg.set_content(body_text)

        raw_bytes = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        body_payload = {"raw": raw_bytes}
        if original.get("threadId"):
            body_payload["threadId"] = original["threadId"]

        sent = self._execute(
            gmail.users()
            .messages()
            .send(userId="me", body=body_payload)
        )
        return {
            "id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "para": reply_to,
            "assunto": subject,
            "respondido": True,
        }

    def mark_gmail_message_read(self, message_id):
        msg_id = message_id.strip()
        if not msg_id:
            raise GoogleWorkspaceError("Informe o ID do e-mail.")
        self._execute(
            self._gmail_service()
            .users()
            .messages()
            .modify(userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]})
        )
        return {"id": msg_id, "marcado_como_lido": True}

    def trash_gmail_message(self, message_id):
        msg_id = message_id.strip()
        if not msg_id:
            raise GoogleWorkspaceError("Informe o ID do e-mail.")
        self._execute(
            self._gmail_service()
            .users()
            .messages()
            .trash(userId="me", id=msg_id)
        )
        return {"id": msg_id, "enviado_para_lixeira": True}

    def list_gmail_labels(self) -> list[dict]:
        gmail = self._gmail_service()
        resp = self._execute(gmail.users().labels().list(userId="me"))
        user_labels = []
        for lbl in resp.get("labels", []) or []:
            if lbl.get("type") == "user":
                user_labels.append(
                    {
                        "id": lbl.get("id", ""),
                        "nome": lbl.get("name", ""),
                    }
                )
        user_labels.sort(key=lambda x: x["nome"].lower())
        return user_labels

    def find_gmail_label(self, label_name: str) -> dict | None:
        alvo = (label_name or "").strip().lower()
        if not alvo:
            return None
        labels = self.list_gmail_labels()
        for lbl in labels:
            if lbl["nome"].strip().lower() == alvo:
                return lbl
        for lbl in labels:
            nome_l = lbl["nome"].strip().lower()
            if alvo in nome_l or nome_l in alvo:
                return lbl
        return None

    def ensure_gmail_label(self, label_name: str) -> str:
        nome = (label_name or "").strip()
        if not nome:
            raise GoogleWorkspaceError("Informe o nome do marcador/etiqueta.")
        gmail = self._gmail_service()
        exist_resp = self._execute(gmail.users().labels().list(userId="me"))
        for lbl in exist_resp.get("labels", []) or []:
            if (lbl.get("name") or "").strip().lower() == nome.lower():
                return lbl["id"]
        created = self._execute(
            gmail.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": nome,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
        )
        return created["id"]

    def create_gmail_label(self, label_name: str) -> dict:
        nome = (label_name or "").strip()
        if not nome:
            raise GoogleWorkspaceError("Informe o nome do marcador que deseja criar.")
        gmail = self._gmail_service()
        exist_resp = self._execute(gmail.users().labels().list(userId="me"))
        for lbl in exist_resp.get("labels", []) or []:
            if (lbl.get("name") or "").strip().lower() == nome.lower():
                return {
                    "id": lbl["id"],
                    "nome": lbl["name"],
                    "ja_existia": True,
                }
        created = self._execute(
            gmail.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": nome,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
        )
        return {
            "id": created.get("id", ""),
            "nome": created.get("name", nome),
            "criado": True,
        }

    def delete_gmail_label(self, label_name: str) -> dict:
        lbl = self.find_gmail_label(label_name)
        if not lbl:
            raise GoogleWorkspaceError(f"Marcador '{label_name}' não encontrado no Gmail.")
        gmail = self._gmail_service()
        self._execute(gmail.users().labels().delete(userId="me", id=lbl["id"]))
        return {
            "id": lbl["id"],
            "nome": lbl["nome"],
            "apagado": True,
        }

    def list_messages_in_label(self, label_name: str, max_results: int = 15) -> dict:
        lbl = self.find_gmail_label(label_name)
        gmail = self._gmail_service()
        limit = max(1, min(30, int(max_results)))
        if lbl:
            response = self._execute(
                gmail.users()
                .messages()
                .list(userId="me", labelIds=[lbl["id"]], maxResults=limit)
            )
            nome_real = lbl["nome"]
        else:
            q_label = label_name.strip().replace(" ", "-")
            response = self._execute(
                gmail.users()
                .messages()
                .list(userId="me", q=f"label:{q_label}", maxResults=limit)
            )
            nome_real = label_name.strip()

        messages = response.get("messages", []) or []
        results = []
        for msg_ref in messages:
            msg = self._execute(
                gmail.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_ref["id"],
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                )
            )
            headers = self._extract_email_headers(msg.get("payload", {}))
            results.append(
                {
                    "id": msg.get("id"),
                    "de": headers.get("from", ""),
                    "assunto": headers.get("subject", "(Sem assunto)"),
                    "data": headers.get("date", ""),
                    "resumo": msg.get("snippet", ""),
                    "nao_lido": "UNREAD" in (msg.get("labelIds") or []),
                }
            )
        return {
            "marcador": nome_real,
            "quantidade": len(results),
            "emails": results,
        }

    def batch_organize_into_label(
        self,
        message_ids: list[str] | None = None,
        query: str = "",
        label_name: str = "",
        mover_da_entrada: bool = True,
        acao: str = "mover_para_marcador",
        max_messages: int = 200,
    ) -> dict:
        gmail = self._gmail_service()
        ids_alvo: list[str] = []
        seen_ids: set[str] = set()

        for mid in message_ids or []:
            if mid and mid not in seen_ids:
                seen_ids.add(mid)
                ids_alvo.append(mid)

        if query and query.strip():
            resp = self._execute(
                gmail.users()
                .messages()
                .list(
                    userId="me",
                    q=query.strip(),
                    maxResults=max(1, min(500, int(max_messages))),
                )
            )
            for item in resp.get("messages", []) or []:
                mid = item.get("id")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    ids_alvo.append(mid)

        if not ids_alvo:
            return {
                "quantidade_afetada": 0,
                "marcador": label_name,
                "acao": acao,
                "aviso": "Nenhum e-mail encontrado para esse remetente/filtro.",
            }

        acao_norm = (acao or "mover_para_marcador").strip().lower()
        add_labels: list[str] = []
        remove_labels: list[str] = []
        nome_marcador_final = label_name.strip()

        if acao_norm in {"lixeira", "apagar", "excluir", "trash"}:
            add_labels.append("TRASH")
            remove_labels.append("INBOX")
        elif acao_norm in {"remover_do_marcador", "tirar_do_marcador"}:
            if not nome_marcador_final:
                raise GoogleWorkspaceError("Informe o nome do marcador de onde deseja remover os e-mails.")
            lbl = self.find_gmail_label(nome_marcador_final)
            if not lbl:
                raise GoogleWorkspaceError(f"Marcador '{nome_marcador_final}' não encontrado.")
            remove_labels.append(lbl["id"])
            nome_marcador_final = lbl["nome"]
        else:
            if nome_marcador_final:
                lbl_existente = self.find_gmail_label(nome_marcador_final)
                if lbl_existente and lbl_existente["nome"].strip().lower() == nome_marcador_final.lower():
                    label_id = lbl_existente["id"]
                    nome_marcador_final = lbl_existente["nome"]
                else:
                    label_id = self.ensure_gmail_label(nome_marcador_final)
                add_labels.append(label_id)
            if mover_da_entrada or acao_norm == "arquivar":
                remove_labels.append("INBOX")

        body_batch: dict = {"ids": ids_alvo[:1000]}
        if add_labels:
            body_batch["addLabelIds"] = add_labels
        if remove_labels:
            body_batch["removeLabelIds"] = remove_labels

        if add_labels or remove_labels:
            self._execute(
                gmail.users().messages().batchModify(userId="me", body=body_batch)
            )

        return {
            "quantidade_afetada": len(ids_alvo),
            "marcador": nome_marcador_final,
            "acao": acao_norm,
            "movido_da_caixa_de_entrada": "INBOX" in remove_labels,
        }

    def apply_gmail_organization_action(
        self,
        message_id: str,
        acao: str,
        marcador: str = "",
        arquivar: bool = False,
        marcar_lido: bool = True,
    ) -> dict:
        msg_id = (message_id or "").strip()
        if not msg_id:
            raise GoogleWorkspaceError("ID do e-mail ausente.")
        acao_norm = (acao or "").strip().lower()
        gmail = self._gmail_service()

        if acao_norm in {"lixeira", "apagar", "excluir", "trash"}:
            self._execute(gmail.users().messages().trash(userId="me", id=msg_id))
            return {"id": msg_id, "acao_aplicada": "lixeira"}

        add_labels: list[str] = []
        remove_labels: list[str] = []

        if marcar_lido or acao_norm in {"marcar_lido", "lido"}:
            remove_labels.append("UNREAD")

        if acao_norm in {"arquivar", "archive"} or arquivar:
            remove_labels.append("INBOX")

        if acao_norm in {"estrela", "importante", "destacar", "star"}:
            add_labels.append("STARRED")

        if marcador and marcador.strip():
            label_id = self.ensure_gmail_label(marcador.strip())
            if label_id and label_id not in add_labels:
                add_labels.append(label_id)

        if add_labels or remove_labels:
            body_mod: dict[str, list[str]] = {}
            if add_labels:
                body_mod["addLabelIds"] = add_labels
            if remove_labels:
                body_mod["removeLabelIds"] = remove_labels
            self._execute(
                gmail.users().messages().modify(userId="me", id=msg_id, body=body_mod)
            )

        return {
            "id": msg_id,
            "acao_aplicada": acao_norm or "etiquetar",
            "marcador": marcador.strip(),
            "arquivado": "INBOX" in remove_labels,
        }


google_workspace = GoogleWorkspace()