import asyncio
import datetime
import json
import os
import pathlib
import random
import re
import subprocess
import time

import edge_tts
import requests

GEMINI_KEY = os.environ["GEMINI_API_KEY"]
PIXABAY_KEY = os.environ["PIXABAY_API_KEY"]
MODELO = "gemini-flash-latest"
MODELOS_RESERVA = ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
VOZ = "pt-BR-AntonioNeural"
CHAMADA = ("Gostou? Deixa o like, comenta o que achou e compartilha "
           "com quem ama carros. Eu sou o CX Car YT, até a próxima!")
VOLUME_MUSICA = 0.12  # 12%; aumente ou diminua se precisar
LOGO_LARGURA = 150    # largura da logo em pixels (o vídeo tem 1080)
LOGO_MARGEM = 120     # distância da logo até a borda de baixo

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
        try:
            return json.loads(HISTORICO.read_text(encoding="utf-8"))
        except ValueError:
            print("historico.json ilegível; começando do zero.")
    return []


def limpar(texto):
    """Remove símbolos que o YouTube não aceita em título e descrição."""
    return re.sub(r"[<>]", "", texto).strip()


# ---------------------------------------------------------------- Gemini
def gemini(prompt, json_mode=False):
    """Chama o Gemini com novas tentativas e modelos reserva."""
    corpo = {"contents": [{"parts": [{"text": prompt}]}]}
    if json_mode:
        corpo["generationConfig"] = {"responseMimeType": "application/json"}
    ultimo_erro = None
    for modelo in [MODELO] + MODELOS_RESERVA:
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{modelo}:generateContent")
        for tentativa in range(4):
            try:
                r = requests.post(
                    url, headers={"x-goog-api-key": GEMINI_KEY},
                    json=corpo, timeout=180,
                )
            except requests.RequestException as e:
                ultimo_erro = str(e)
                time.sleep(5 * (tentativa + 1))
                continue
            if r.status_code == 200:
                try:
                    return r.json()["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError, ValueError):
                    ultimo_erro = "resposta vazia ou bloqueada"
                    print("Gemini:", ultimo_erro, modelo)
                    time.sleep(5)
                    continue
            ultimo_erro = f"{r.status_code}: {r.text[:200]}"
            print("Gemini erro", modelo, ultimo_erro)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(10 * (tentativa + 1))
                continue
            break
    raise SystemExit(f"Gemini falhou: {ultimo_erro}")


def ler_json(texto):
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.MULTILINE).strip()
    ini, fim = texto.find("{"), texto.rfind("}")
    if ini != -1 and fim != -1:
        texto = texto[ini:fim + 1]
    return json.loads(texto)


def gemini_json(prompt, validar):
    for _ in range(3):
        texto = gemini(prompt, json_mode=True)
        try:
            dados = ler_json(texto)
            if isinstance(dados, dict) and validar(dados):
                return dados
        except ValueError:
            pass
        print("Resposta do Gemini fora do formato; tentando de novo.")
    raise SystemExit("O Gemini não devolveu o formato esperado.")


def escolher_tema(lista_usados):
    categoria = random.choice(CATEGORIAS)
    usados = [h["tema"] for h in lista_usados][-80:]
    prompt = f"""Sugira UMA história REAL e interessante do mundo dos carros, na categoria: {categoria}.
Regras:
- Deve girar em torno de um carro ou marca específica, e ter acontecido de verdade.
- Deve existir artigo sobre ela na Wikipédia em português.
- NÃO repita nem chegue perto destes temas já usados: {usados}
Responda só em JSON:
{{"tema": "frase curta do tema", "marca": "só a marca, ex: Ferrari", "carro": "marca e modelo, ex: Ferrari F40", "busca_wikipedia": "termos para achar o artigo"}}"""
    tema = gemini_json(
        prompt,
        lambda d: all(d.get(k) for k in
                      ("tema", "marca", "carro", "busca_wikipedia")),
    )
    return {k: str(tema[k]) for k in
            ("tema", "marca", "carro", "busca_wikipedia")}


# ------------------------------------------------------------- Wikipédia
def _get_wikipedia(lang, params):
    """Pedido à Wikipédia com identificação, novas tentativas e checagem."""
    api = f"https://{lang}.wikipedia.org/w/api.php"
    cabecalho = {
        "User-Agent": ("CXCarYTBot/1.0 "
                       "(https://github.com/Erickdz77/cx-car-YouTube; "
                       "bot de curiosidades sobre carros)"),
        "Accept": "application/json",
    }
    for tentativa in range(3):
        try:
            r = requests.get(api, headers=cabecalho, params=params, timeout=60)
        except requests.RequestException as e:
            print("Wikipédia erro de rede:", e)
            time.sleep(3 * (tentativa + 1))
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                print("Wikipédia respondeu algo que não é JSON:", r.text[:150])
        else:
            print("Wikipédia erro", r.status_code, r.text[:150])
        time.sleep(3 * (tentativa + 1))
    return None


def pesquisar_wikipedia(busca, marca):
    """Acha um artigo que cite a marca. Tenta em português e depois em inglês."""
    marca_min = marca.lower().split()[0]
    for lang in ("pt", "en"):
        dados = _get_wikipedia(lang, {
            "action": "query", "list": "search", "srsearch": busca,
            "srlimit": 3, "format": "json",
        })
        if not dados:
            continue
        for achado in dados.get("query", {}).get("search", []):
            dados2 = _get_wikipedia(lang, {
                "action": "query", "prop": "extracts", "explaintext": 1,
                "redirects": 1, "titles": achado["title"], "format": "json",
            })
            if not dados2:
                continue
            paginas = dados2.get("query", {}).get("pages", {})
            texto = next(iter(paginas.values()), {}).get("extract", "")
            if len(texto) >= 500 and marca_min in texto.lower():
                print(f"Fonte: Wikipédia ({lang}), {achado['title']}, "
                      f"{len(texto)} caracteres")
                return texto[:7000]
    return ""


# --------------------------------------------------------------- Roteiro
def escrever_roteiro(tema, fonte):
    formato = random.choice(FORMATOS)
    prompt = f"""Você é roteirista de vídeos curtos virais sobre carros, em português do Brasil.
Tema: {tema['tema']} ({tema['carro']})
Formato: {formato}

Use SOMENTE fatos presentes no texto-fonte abaixo (que pode estar em português
ou inglês). Escreva a narração sempre em português do Brasil.
Não invente datas, números ou nomes. Se a fonte não trouxer um detalhe,
não o mencione.

Se o texto-fonte NÃO confirmar claramente a história do tema, responda apenas:
{{"erro": "fonte nao confirma"}}

Estrutura obrigatória:
1. Gancho forte nos primeiros 3 segundos (uma frase que gera curiosidade).
2. Desenvolvimento com uma reviravolta.
3. Final que fecha a história ou deixa uma pergunta curiosa.

Narração entre 110 e 140 palavras, frases curtas, tom empolgado e natural, sem emojis,
sem "olá pessoal" e sem pedir para curtir ou seguir (a chamada final é adicionada depois).

TEXTO-FONTE:
{fonte}

Responda só em JSON:
{{"titulo": "título curto e chamativo, até 70 caracteres",
  "narracao": "texto completo da narração",
  "termos_busca": ["3 a 4 termos em inglês para buscar vídeos do carro, ex: ferrari f40 driving"]}}"""
    return gemini_json(
        prompt,
        lambda d: d.get("erro") or (d.get("narracao") and d.get("titulo")),
    )


def preparar_roteiro(historico):
    """Tenta até 4 temas até achar um com fonte confiável."""
    tentados = list(historico)
    for _ in range(4):
        tema = escolher_tema(tentados)
        tentados.append({"tema": tema["tema"]})
        print("Tema:", tema["tema"])
        fonte = pesquisar_wikipedia(tema["busca_wikipedia"], tema["marca"])
        if not fonte:
            print("Sem fonte confiável; tentando outro tema.")
            continue
        roteiro = escrever_roteiro(tema, fonte)
        if roteiro.get("erro") or not roteiro.get("narracao"):
            print("A fonte não confirma o tema; tentando outro.")
            continue
        return tema, roteiro
    raise SystemExit("Não achei um tema com fonte confiável. Rode de novo.")


# ------------------------------------------------------------ Voz e vídeo
async def _tts(texto, caminho):
    await edge_tts.Communicate(texto, VOZ, rate="+8%").save(str(caminho))


def gerar_voz(texto, caminho):
    for tentativa in range(4):
        try:
            asyncio.run(_tts(texto, caminho))
            if caminho.exists() and caminho.stat().st_size > 1000:
                return
        except Exception as e:
            print("Erro na voz:", e)
        time.sleep(5 * (tentativa + 1))
    raise SystemExit("Não consegui gerar a voz (edge-tts).")


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
        try:
            r = requests.get(
                "https://pixabay.com/api/videos/",
                params={"key": PIXABAY_KEY, "q": termo[:100], "per_page": 20,
                        "safesearch": "true"},
                timeout=60,
            )
            if r.status_code != 200:
                print("Pixabay erro:", r.status_code)
                continue
            hits = r.json().get("hits", [])
        except (requests.RequestException, ValueError) as e:
            print("Pixabay erro:", e)
            continue
        for v in hits:
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
        try:
            with requests.get(info["url"], stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(destino, "wb") as f:
                    for pedaco in r.iter_content(1 << 20):
                        f.write(pedaco)
            arquivos.append(destino)
        except requests.RequestException as e:
            print("Falha ao baixar um vídeo, ignorando:", e)
    return arquivos


def montar_fundo(arquivos, dur_total):
    """Corta e padroniza os clipes em 9:16 até cobrir a duração da narração."""
    usaveis = list(arquivos)
    random.shuffle(usaveis)
    segmentos, soma, i = [], 0.0, 0
    while soma < dur_total + 1 and i < 120 and usaveis:
        origem = usaveis[i % len(usaveis)]
        seg = TMP / f"seg_{i}.mp4"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-t", "4",
                 "-i", str(origem),
                 "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
                        "crop=1080:1920,fps=30,setsar=1",
                 "-an", "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "23", str(seg)],
                check=True,
            )
            soma += duracao(seg)
            segmentos.append(seg)
        except (subprocess.CalledProcessError, ValueError):
            print("Clipe com problema, ignorando:", origem.name)
            usaveis.remove(origem)
        i += 1
    if not segmentos:
        raise SystemExit("Nenhum clipe de vídeo utilizável.")
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


# ------------------------------------------------------------------ Main
def main():
    historico = carregar_historico()
    tema, roteiro = preparar_roteiro(historico)

    texto_roteiro = re.sub(r"\s+", " ", str(roteiro["narracao"])).strip()
    narracao = texto_roteiro + " " + CHAMADA

    audio = TMP / "voz.mp3"
    voz_texto = narracao.replace("CX Car YT", "Cê Xis Car Uai Tê")
    gerar_voz(voz_texto, audio)
    dur = duracao(audio)

    termos = roteiro.get("termos_busca") or []
    if isinstance(termos, str):
        termos = [termos]
    termos = [str(t) for t in termos][:4] or [tema["carro"]]

    brutos = baixar_videos(termos, tema["marca"])
    if not brutos:
        raise SystemExit("Nenhum vídeo encontrado no Pixabay.")
    fundo = montar_fundo(brutos, dur)

    gerar_legenda(narracao, dur, pathlib.Path("legenda.srt"))
    agora = datetime.datetime.now(datetime.timezone.utc)
    nome = agora.strftime("%Y%m%d_%H%M")
    final = SAIDA / f"{nome}.mp4"
    estilo = ("FontName=DejaVu Sans,Bold=1,FontSize=11,Alignment=2,MarginV=70,"
              "Outline=3,PrimaryColour=&H00FFFFFF")

    logo = pathlib.Path("logo.png")
    musicas = sorted(pathlib.Path("musicas").glob("*.mp3"))
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-i", str(fundo), "-i", str(audio)]
    proximo = 2
    idx_musica = idx_logo = None
    if musicas:
        musica = random.choice(musicas)
        print("Música:", musica.name)
        cmd += ["-stream_loop", "-1", "-i", str(musica)]
        idx_musica = proximo
        proximo += 1
    if logo.exists():
        cmd += ["-i", str(logo)]
        idx_logo = proximo
        proximo += 1

    partes = [f"[0:v]subtitles=legenda.srt:force_style='{estilo}'[sv]"]
    if idx_logo is not None:
        partes.append(f"[{idx_logo}:v]scale={LOGO_LARGURA}:-1[lg]")
        partes.append(f"[sv][lg]overlay=(W-w)/2:H-h-{LOGO_MARGEM}[vout]")
    else:
        partes.append("[sv]null[vout]")
    if idx_musica is not None:
        partes.append(
            f"[1:a]volume=1.0[v];[{idx_musica}:a]volume={VOLUME_MUSICA}[m];"
            "[v][m]amix=inputs=2:duration=first:normalize=0,"
            f"afade=t=out:st={max(dur - 2, 0):.2f}:d=2[aout]")
        mapa_audio = "[aout]"
    else:
        mapa_audio = "1:a"
    cmd += ["-filter_complex", ";".join(partes),
            "-map", "[vout]", "-map", mapa_audio,
            "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-c:a", "aac", "-b:a", "160k", str(final)]
    subprocess.run(cmd, check=True)

    (SAIDA / f"{nome}.json").write_text(
        json.dumps(
            {"titulo": limpar(str(roteiro["titulo"]))[:95],
             "descricao": limpar(texto_roteiro) + "\n\nVídeos: Pixabay",
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
