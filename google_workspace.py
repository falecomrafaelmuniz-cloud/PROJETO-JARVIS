import threading
import re
from datetime import datetime, timezone
from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload


BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/documents",
]
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

    def _credentials(self):
        with self._lock:
            if not TOKEN_PATH.is_file():
                raise GoogleWorkspaceError(
                    "OAuth ainda não foi configurado. Coloque credentials.json na pasta "
                    "do projeto e execute: python configurar_google.py"
                )

            try:
                credentials = Credentials.from_authorized_user_file(
                    str(TOKEN_PATH),
                    SCOPES,
                )
            except (OSError, ValueError, GoogleAuthError) as error:
                raise GoogleWorkspaceError(
                    f"Não foi possível ler token.json; execute python configurar_google.py novamente: {error}"
                ) from error

            if not credentials.has_scopes(SCOPES):
                raise GoogleWorkspaceError(
                    "O token não possui todos os escopos necessários. "
                    "Execute python configurar_google.py novamente."
                )

            if credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                    TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
                except (GoogleAuthError, OSError) as error:
                    raise GoogleWorkspaceError(
                        f"Não foi possível renovar a autorização Google: {error}"
                    ) from error

            if not credentials.valid:
                raise GoogleWorkspaceError(
                    "A autorização Google expirou. Execute python configurar_google.py novamente."
                )
            return credentials

    def _service(self, api_name, version):
        with self._lock:
            if api_name not in self._services:
                self._services[api_name] = build(
                    api_name,
                    version,
                    credentials=self._credentials(),
                    cache_discovery=False,
                )
            return self._services[api_name]

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

    def list_calendar_events(self, time_min="", time_max=""):
        if not time_min.strip():
            time_min = datetime.now(timezone.utc).isoformat()
        parameters = {
            "calendarId": "primary",
            "timeMin": time_min,
            "maxResults": 50,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if time_max.strip():
            parameters["timeMax"] = time_max
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
        event = self._execute(
            self._service("calendar", "v3")
            .events()
            .insert(
                calendarId="primary",
                body={
                    "summary": summary.strip(),
                    "description": description,
                    "start": {"dateTime": start_iso},
                    "end": {"dateTime": end_iso},
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
            changes["start"] = {"dateTime": start_iso}
        if end_iso.strip():
            changes["end"] = {"dateTime": end_iso}
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


google_workspace = GoogleWorkspace()