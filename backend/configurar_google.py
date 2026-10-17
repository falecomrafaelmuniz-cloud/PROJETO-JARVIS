from google_auth_oauthlib.flow import InstalledAppFlow

from google_workspace import CREDENTIALS_PATH, SCOPES, TOKEN_PATH


def main():
    if not CREDENTIALS_PATH.is_file():
        raise FileNotFoundError(
            f"Não encontrei {CREDENTIALS_PATH.name}. Baixe o OAuth de aplicativo "
            "para computador no Google Cloud Console e coloque-o nesta pasta."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_PATH),
        SCOPES,
    )
    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        access_type="offline",
        prompt="consent",
    )
    TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Autorização concluída. Token salvo em {TOKEN_PATH.name}.")


if __name__ == "__main__":
    main()