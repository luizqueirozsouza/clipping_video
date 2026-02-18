import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

# Escopos para Drive e YouTube
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/youtube.upload",
]


def get_authenticated_creds(token_filename="token.json"):
    """Gerencia autenticação usando um arquivo de token específico"""
    creds = None
    if os.path.exists(token_filename):
        try:
            creds = Credentials.from_authorized_user_file(token_filename, SCOPES)
        except:
            creds = None

    # Se não tiver credencial válida, força o login
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except:
                if os.path.exists(token_filename):
                    os.remove(token_filename)
                return None
        else:
            if not os.path.exists("client_secret.json"):
                raise FileNotFoundError("❌ 'client_secret.json' não encontrado.")

            # Inicia o fluxo de login
            flow = InstalledAppFlow.from_client_secrets_file(
                "client_secret.json", SCOPES
            )

            # === CORREÇÃO: prompt='select_account' para forçar a lista de contas ===
            creds = flow.run_local_server(port=0, prompt="select_account")

        # Salva no arquivo ESPECÍFICO do canal selecionado
        with open(token_filename, "w") as token:
            token.write(creds.to_json())

    return creds


# === DRIVE ===
def upload_to_drive(file_path, folder_id=None, rename_to=None, token_file="token.json"):
    creds = get_authenticated_creds(token_file)
    if not creds:
        return False, "Falha Auth"

    service = build("drive", "v3", credentials=creds)
    name = rename_to if rename_to else os.path.basename(file_path)
    metadata = {"name": name}
    if folder_id:
        metadata["parents"] = [folder_id]

    # Determina MIME type (video ou imagem)
    mime = "video/mp4" if file_path.lower().endswith(".mp4") else "image/jpeg"
    media = MediaFileUpload(file_path, mimetype=mime, resumable=True)

    try:
        f = (
            service.files()
            .create(body=metadata, media_body=media, fields="id")
            .execute()
        )
        return True, f.get("id")
    except Exception as e:
        return False, str(e)


# === YOUTUBE ===
def upload_to_youtube(
    file_path, title, description, tags=[], privacy="private", token_file="token.json"
):
    creds = get_authenticated_creds(token_file)
    if not creds:
        return False, "Falha Auth"

    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags,
            "categoryId": "22",  # People & Blogs
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }

    media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=True)

    try:
        req = youtube.videos().insert(
            part=",".join(body.keys()), body=body, media_body=media
        )
        res = None
        while res is None:
            status, res = req.next_chunk()
        return True, res.get("id")
    except HttpError as e:
        if e.resp.status == 403:
            return False, "Cota Excedida"
        return False, str(e)
    except Exception as e:
        return False, str(e)
