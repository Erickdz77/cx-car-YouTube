import json
import os
import pathlib
import sys

import requests

PRIVACIDADE = "public"  # "public", "unlisted" ou "private"


def token_de_acesso():
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": os.environ["YT_CLIENT_ID"],
            "client_secret": os.environ["YT_CLIENT_SECRET"],
            "refresh_token": os.environ["YT_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=60,
    )
    if r.status_code != 200:
        print("Erro ao renovar o token:", r.status_code, r.text[:300])
        print("Se aparecer invalid_grant, gere um Refresh token novo.")
        sys.exit(1)
    return r.json()["access_token"]


def main():
    if not os.environ.get("YT_REFRESH_TOKEN"):
        print("Sem YT_REFRESH_TOKEN; pulando o upload.")
        return

    videos = sorted(pathlib.Path("saida").glob("*.mp4"))
    if not videos:
        print("Nenhum vídeo em saida/.")
        sys.exit(1)
    video = videos[-1]
    info = json.loads(video.with_suffix(".json").read_text(encoding="utf-8"))

    titulo = info["titulo"][:95]
    descricao = info["descricao"] + "\n\n" + info.get("hashtags", "") + " #Shorts"
    corpo = {
        "snippet": {
            "title": titulo,
            "description": descricao[:4900],
            "categoryId": "2",
            "defaultLanguage": "pt",
            "tags": ["carros", "curiosidades", "shorts", "CX Car YT"],
        },
        "status": {
            "privacyStatus": PRIVACIDADE,
            "selfDeclaredMadeForKids": False,
        },
    }

    token = token_de_acesso()
    tamanho = video.stat().st_size
    r = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(tamanho),
        },
        data=json.dumps(corpo).encode("utf-8"),
        timeout=60,
    )
    if r.status_code != 200:
        print("Erro ao iniciar o upload:", r.status_code, r.text[:500])
        sys.exit(1)

    url = r.headers["Location"]
    with open(video, "rb") as f:
        r = requests.put(
            url, data=f, headers={"Content-Type": "video/mp4"}, timeout=900
        )
    if r.status_code not in (200, 201):
        print("Erro no envio do vídeo:", r.status_code, r.text[:500])
        sys.exit(1)

    dados = r.json()
    print("Enviado! https://youtube.com/shorts/" + dados["id"])
    print("Privacidade:", dados.get("status", {}).get("privacyStatus"))


if __name__ == "__main__":
    main()
