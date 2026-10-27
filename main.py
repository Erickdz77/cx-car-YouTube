import asyncio
import datetime
import json
import os
import pathlib
import random
import re
import subprocess

import edge_tts
import requests

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
PIXABAY_KEY = os.environ["PIXABAY_API_KEY"]
MODELO = "gemini-flash-latest"  # se der erro de modelo, troque por outro nome
VOZ = "pt-BR-AntonioNeural"

SAIDA = pathlib.Path("saida")
TMP = pathlib.Path("tmp")
HISTORICO = pathlib.Path("historico.json")
SAIDA.mkdir(exist_ok=True)
TMP.mkdir(exist_ok=True)

CATEGORIAS = [
    "rivalidades lendárias entre marcas ou pilotos",
    "carros raros e de produção limitadíssima",
    "projetos que quase faliram uma marca",
    "bastidores de corridas famosas",
    "erros de fábrica e recalls famosos",
    "carros que viraram lenda por um motivo inesperado",
    "origem do nome ou do logotipo de marcas",
    "protótipos e carros conceito que nunca chegaram às ruas",
    "carros de filmes e celebridades",
    "viradas históricas: marcas que quase sumiram e voltaram",
]
FORMATOS = [
    "mistério que só se resolve no final",
    "'você sabia?' com uma revelação surpreendente",
    "rivalidade com dois lados",
    "história de superação com reviravolta",
    "contagem de fatos chocantes",
]


def carregar_historico():
    if HISTORICO.exists():
        return json.loads(HISTORICO.read_text(encoding="utf-8"))
    return []


def gemini(prompt, json_mode=False):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODELO}:generateContent"
    corpo = {"contents": [{"parts": [{"text": prompt}]}]}
    if json_mode:
        corpo["generationConfig"] = {"responseMimeType": "application/json"}
    r = requests.post(
        url, headers={"x-goog-api-key": GEMINI_KEY}, json=corpo, timeout=180
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def escolher_tema(historico):
    categoria = random.choice(CATEGORIAS)
    usados = [h["tema"] for h in historico][-80:]
    prompt = f"""Sugira UMA história REAL e interessante do mundo dos carros, na categoria: {categoria}.
Regras:
- Deve girar em torno de um carro ou marca específica.
- Deve existir artigo sobre ela na Wikipédia em português.
- NÃO repita nem chegue perto destes temas já usados: {usados}
Responda só em JSON:
{{"tema": "frase curta do tema", "marca": "só a marca, ex: Ferrari", "carro": "marca e modelo, ex: Ferrari F40", "busca_wikipedia": "termos para achar o artigo"}}"""
    return json.loads(gemini(prompt, json_mode=True))


def pesquisar_wikipedia(busca):
    api = "https://pt.wikipedia.org/w/api.php"
    h = {"User-Agent": "carros-shorts-bot/1.0"}
    r = requests.get(
        api,
        headers=h,
        params={"action": "query", "list": "search", "srsearch": busca,
                "srlimit": 1, "format": "json"},
        timeout=60,
    )
    achados = r.json()["query"]["search"]
    if not achados:
        return ""
    titulo = achados[0]["title"]
    r = requests.get(
        api,
        headers=h,
        params={"action": "query", "prop": "extracts", "explaintext": 1,
                "titles": titulo, "redirects": 1, "format": "json"},
        timeout=60,
    )
    paginas = r.json()["query"]["pages"]
    texto = next(iter(paginas.values())).get("extract", "")
    return texto[:7000]


def escrever_roteiro(tema, fonte):
    formato = random.choice(FORMATOS)
    prompt = f"""Você é roteirista de vídeos curtos virais sobre carros, em português do Brasil.
Tema: {tema['tema']} ({tema['carro']})
Formato: {formato}

Use SOMENTE fatos presentes no texto-fonte abaixo. Não invente datas, números ou nomes.
Se a fonte não trouxer um detalhe, não o mencione.

Estrutura obrigatória:
1. Gancho forte nos primeiros 3 segundos (uma frase que gera curiosidade).
2. Desenvolvimento com uma reviravolta.
3. Final que fecha a história ou deixa uma pergunta curiosa.

Narração entre 110 e 140 palavras, frases curtas, tom empolgado e natural, sem emojis,
sem "olá pessoal" e sem pedir para curtir ou seguir.

TEXTO-FONTE:
{fonte}

Responda só em JSON:
{{"titulo": "título curto e chamativo, até 70 caracteres",
  "narracao": "texto completo da narração",
  "termos_busca": ["3 a 4 termos em inglês para buscar vídeos do carro, ex: ferrari f40 driving"]}}"""
    return json.loads(gemini(prompt, json_mode=True))


async def _tts(texto, caminho):
    await edge_tts.Communicate(texto, VOZ, rate="+8%").save(str(caminho))


def duracao(caminho):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(caminho)]
    )
    return float(out.decode().strip())


def baixar_videos(termos, marca):
    """Busca vídeos no Pixabay e filtra pelos que citam a marca nas tags."""
    marca = marca.lower()
    marca_url = marca.replace(" ", "-")
    vistos, certos, genericos = set(), [], []
    for termo in termos + [f"{marca} car"]:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": PIXABAY_KEY, "q": termo[:100], "per_page": 20,
                    "safesearch": "true"},
            timeout=60,
        )
        if r.status_code != 200:
            print("Pixabay erro:", r.status_code)
            continue
        for v in r.json().get("hits", []):
            if v["id"] in vistos:
                continue
            vistos.add(v["id"])
            texto = (v.get("tags", "") + " " + v.get("pageURL", "")).lower()
            if marca in texto or marca_url in texto:
                certos.append(v)
            else:
                genericos.append(v)
    escolhidos = certos[:8]
    if len(escolhidos) < 4:  # plano B: completa com vídeos genéricos
        escolhidos += genericos[: 6 - len(escolhidos)]
    print(f"Vídeos da marca: {len(certos)} | usados: {len(escolhidos)}")

    arquivos = []
    for v in escolhidos:
        opcoes = v.get("videos", {})
        info = opcoes.get("medium") or opcoes.get("large") or opcoes.get("small")
        if not info or not info.get("url"):
            continue
        destino = TMP / f"bruto_{v['id']}.mp4"
        with requests.get(info["url"], stream=True, timeout=120) as r:
            with open(destino, "wb") as f:
                for pedaco in r.iter_content(1 << 20):
                    f.write(pedaco)
        arquivos.append(destino)
    return arquivos


def montar_fundo(arquivos, dur_total):
    """Corta e padroniza os clipes em 9:16 até cobrir a duração da narração."""
    random.shuffle(arquivos)
    segmentos, soma, i = [], 0.0, 0
    while soma < dur_total + 1 and i < 60:
        origem = arquivos[i % len(arquivos)]
        seg = TMP / f"seg_{i}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-t", "4", "-i", str(origem),
             "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
                    "crop=1080:1920,fps=30,setsar=1",
             "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             str(seg)],
            check=True,
        )
        soma += duracao(seg)
        segmentos.append(seg)
        i += 1
    lista = TMP / "lista.txt"
    lista.write_text("".join(f"file '{s.name}'\n" for s in segmentos))
    fundo = TMP / "fundo.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(lista), "-c", "copy", str(fundo)],
        check=True,
    )
    return fundo


def _t(seg):
    ms = int(seg * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def gerar_legenda(texto, dur, caminho):
    palavras = texto.split()
    grupos = [palavras[i:i + 3] for i in range(0, len(palavras), 3)]
    pesos = [sum(len(p) + 1 for p in g) for g in grupos]
    total = sum(pesos)
    t, linhas = 0.0, []
    for n, (g, p) in enumerate(zip(grupos, pesos), 1):
        fim = t + dur * p / total
        linhas.append(f"{n}\n{_t(t)} --> {_t(fim)}\n{' '.join(g).upper()}\n")
        t = fim
    caminho.write_text("\n".join(linhas), encoding="utf-8")


def main():
    historico = carregar_historico()
    tema = escolher_tema(historico)
    print("Tema:", tema["tema"])

    fonte = pesquisar_wikipedia(tema["busca_wikipedia"])
    if len(fonte) < 500:
        raise SystemExit("Fonte fraca na Wikipédia; rode de novo para sortear outro tema.")

    roteiro = escrever_roteiro(tema, fonte)
    narracao = re.sub(r"\s+", " ", roteiro["narracao"]).strip()

    audio = TMP / "voz.mp3"
    asyncio.run(_tts(narracao, audio))
    dur = duracao(audio)

    brutos = baixar_videos(roteiro["termos_busca"], tema["marca"])
    if not brutos:
        raise SystemExit("Nenhum vídeo encontrado no Pixabay.")
    fundo = montar_fundo(brutos, dur)

    gerar_legenda(narracao, dur, pathlib.Path("legenda.srt"))
    nome = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M")
    final = SAIDA / f"{nome}.mp4"
    estilo = ("FontName=DejaVu Sans,Bold=1,FontSize=11,Alignment=2,MarginV=70,"
              "Outline=3,PrimaryColour=&H00FFFFFF")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(fundo), "-i", str(audio),
         "-vf", f"subtitles=legenda.srt:force_style='{estilo}'",
         "-map", "0:v", "-map", "1:a", "-t", f"{dur:.2f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-c:a", "aac", "-b:a", "160k", str(final)],
        check=True,
    )

    (SAIDA / f"{nome}.json").write_text(
        json.dumps(
            {"titulo": roteiro["titulo"],
             "descricao": narracao + "\n\nVídeos: Pixabay",
             "hashtags": "#carros #shorts #curiosidades"},
            ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    historico.append({"tema": tema["tema"], "carro": tema["carro"], "data": nome})
    HISTORICO.write_text(json.dumps(historico, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    print("Vídeo pronto:", final)


if __name__ == "__main__":
    main()
