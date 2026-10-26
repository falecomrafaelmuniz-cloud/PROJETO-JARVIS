import os
import sys
import webbrowser
from google_auth_oauthlib.flow import InstalledAppFlow

from google_workspace import CREDENTIALS_PATH, SCOPES, TOKEN_PATH


class _WindowsBrowser(webbrowser.BaseBrowser):
    def open(self, url, new=0, autoraise=True):
        print(f"\n[OAUTH_URL] {url}\n", flush=True)
        try:
            os.startfile(url)
            return True
        except Exception:
            return False


def main():
    if not CREDENTIALS_PATH.is_file():
        raise FileNotFoundError(
            f"Não encontrei {CREDENTIALS_PATH.name}. Baixe o OAuth de aplicativo "
            "para computador no Google Cloud Console e coloque-o nesta pasta."
        )

    webbrowser.register("windows-startfile", None, _WindowsBrowser(), preferred=True)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_PATH),
        SCOPES,
    )
    print("Iniciando servidor local para autorização Google...", flush=True)
    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        access_type="offline",
        prompt="consent",
        open_browser=True,
    )
    TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Autorização concluída com sucesso! Token salvo em {TOKEN_PATH.name}.", flush=True)


if __name__ == "__main__":
    main()
