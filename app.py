from datetime import datetime
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
from io import BytesIO
import json
import math
import re
import secrets
import socket
import threading
import unicodedata
import urllib.parse
import webbrowser

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    requests = None
    HAS_REQUESTS = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    Image = None
    HAS_PIL = False

try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    qrcode = None
    HAS_QRCODE = False


BASE_LAT = -22.6940472
BASE_LON = -47.6552268
ORIGEM_ORCAMENTO_LABEL = "Piracicaba - SP"

USUARIO_REMOTO = "charliesp"
SENHA_REMOTA = "102030"
CHAVE_SESSAO = secrets.token_bytes(32)
NOME_COOKIE_SESSAO = "aic_calc_sessao"

PASSAGENS_REFERENCIA_SP = {
    "AC": 2450.00, "AL": 1650.00, "AP": 2300.00, "AM": 2200.00, "BA": 1250.00,
    "CE": 1550.00, "DF": 780.00, "ES": 820.00, "GO": 790.00, "MA": 1750.00,
    "MT": 1150.00, "MS": 920.00, "MG": 680.00, "PA": 1850.00, "PB": 1620.00,
    "PR": 560.00, "PE": 1520.00, "PI": 1700.00, "RJ": 540.00, "RN": 1650.00,
    "RS": 790.00, "RO": 2100.00, "RR": 2650.00, "SC": 620.00, "SE": 1580.00,
    "TO": 1480.00,
}

PRACAS_PEDAGIO_SP = [
    ("Saltinho (SP-308)", -22.8458, -47.6244, 11.40),
    ("Capivari (SP-308)", -22.9765, -47.5236, 11.40),
    ("Santa Bárbara d'Oeste (SP-304)", -22.7539, -47.4142, 11.80),
    ("Sumaré / Hortolândia (SP-330 Anhanguera)", -22.8390, -47.2115, 13.90),
    ("Valinhos / Vinhedo (SP-330 Anhanguera)", -23.0142, -46.9936, 13.90),
    ("Campo Limpo Paulista / Caieiras (SP-348 Bandeirantes)", -23.2580, -46.7940, 14.60),
    ("Perus / Franco da Rocha (SP-348 Bandeirantes)", -23.3680, -46.7420, 14.60),
    ("Rio Claro (SP-310 Washington Luís)", -22.4450, -47.5580, 12.60),
    ("Araras (SP-330 Anhanguera)", -22.3560, -47.3840, 13.90),
    ("Pirassununga (SP-330 Anhanguera)", -21.9950, -47.4250, 13.90),
    ("São Carlos / Itirapina (SP-310 Washington Luís)", -22.1480, -47.7420, 12.60),
    ("Araraquara (SP-310 Washington Luís)", -21.7940, -48.1760, 12.60),
    ("Jaboticabal / Guariba (SP-333 / SP-326)", -21.3560, -48.3520, 12.10),
    ("Itu (SP-075)", -23.2640, -47.2990, 12.90),
    ("Boituva (SP-280 Castelo Branco)", -23.2830, -47.6740, 13.40),
    ("Araçariguama / São Roque (SP-280 Castelo Branco)", -23.4370, -47.0630, 13.40),
    ("Itatiba (SP-065 Dom Pedro I)", -23.0050, -46.8420, 15.20),
    ("Igaratá (SP-065 Dom Pedro I)", -23.2040, -46.1560, 15.20),
    ("Jacareí / São José dos Campos (SP-070 Carvalho Pinto)", -23.2620, -45.9280, 14.40),
    ("Cubatão / Imigrantes (SP-160)", -23.8420, -46.4250, 29.80),
    ("Jaú / Brotas (SP-225)", -22.2950, -48.5580, 11.90),
    ("Agudos / Bauru (SP-225)", -22.3340, -48.9860, 11.90),
]


def _gerar_token_sessao(usuario: str) -> str:
    payload = usuario.encode("utf-8")
    assinatura = hmac.new(CHAVE_SESSAO, payload, hashlib.sha256).hexdigest()
    return f"{usuario}:{assinatura}"


def _token_sessao_valido(token: str) -> bool:
    if not token or ":" not in token:
        return False
    usuario, assinatura = token.split(":", 1)
    esperado = hmac.new(CHAVE_SESSAO, usuario.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(usuario, USUARIO_REMOTO) and hmac.compare_digest(assinatura, esperado)


def normalizar_texto(texto: str) -> str:
    limpo = re.sub(r"\s+", " ", texto or "").strip().lower()
    return "".join(
        ch for ch in unicodedata.normalize("NFD", limpo)
        if unicodedata.category(ch) != "Mn"
    )


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def periculosidade_por_cidade(cidade: str) -> float:
    return 50.00 if normalizar_texto(cidade) == "piracicaba" else 100.00


def obter_ip_local() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"
    finally:
        sock.close()


def gerar_qrcode_png_bytes(url: str, salvar_arquivo: str = "qrcode_calculadora_mobile.png") -> bytes | None:
    if not (HAS_QRCODE and qrcode and HAS_PIL and Image):
        return None
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    if salvar_arquivo:
        img.save(salvar_arquivo)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def gerar_qrcode_ascii(url: str) -> str:
    if not (HAS_QRCODE and qrcode):
        return f"[Instale 'qrcode[pil]' para QR em imagem: pip install qrcode[pil]]\nURL: {url}"
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()


def buscar_combustivel_sp() -> tuple[float, str]:
    padrao = 6.09
    if not (HAS_REQUESTS and requests):
        return padrao, "Referência ANP Piracicaba/SP (sem requests)"
    try:
        resp = requests.get(
            "https://precos.petrobras.com.br/selecao-de-estados-gasolina",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=6,
        )
        if resp.status_code == 200:
            match = re.search(r"São Paulo.*?R\$\s*(\d+[,\.]\d{2})", resp.text, re.DOTALL | re.IGNORECASE)
            if match:
                return float(match.group(1).replace(",", ".")), "Petrobras / ANP (SP)"
    except Exception:
        pass
    return padrao, "Referência ANP Piracicaba/SP"


def buscar_passagem_referencia(cidade: str, uf: str) -> tuple[float, str]:
    uf_limpa = (uf or "SP").strip().upper()
    if uf_limpa == "SP":
        return 0.0, "Isento (Estado de SP)"
    if HAS_REQUESTS and requests:
        try:
            consulta = f"passagem aerea ida e volta {cidade} {uf_limpa} preco reais"
            resp = requests.get("https://duckduckgo.com/html/", params={"q": consulta}, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
            if resp.status_code == 200:
                valores = re.findall(r"R\$\s*(\d{3,4}(?:[.,]\d{2})?)", resp.text)
                nums = []
                for v in valores:
                    try:
                        n = float(v.replace(".", "").replace(",", "."))
                        if 350.0 <= n <= 5500.0:
                            nums.append(n)
                    except ValueError:
                        continue
                if nums:
                    return round(sum(nums[:3]) / len(nums[:3]), 2), f"Pesquisa Web ({cidade}-{uf_limpa})"
        except Exception:
            pass
    return PASSAGENS_REFERENCIA_SP.get(uf_limpa, 1350.00), f"Tabela de referência SP->{uf_limpa}"


def estimar_rota_e_pedagios(cidade: str, uf: str, bairro: str) -> dict:
    cidade_limpa = (cidade or "").strip()
    uf_limpa = (uf or "SP").strip().upper()
    bairro_limpo = (bairro or "").strip()

    if not cidade_limpa:
        return {
            "km_ida": 0.0,
            "qtd_pedagios": 0,
            "valor_pedagio_un": 0.0,
            "pedagio_ida_volta": 0.0,
            "dest_lat": None,
            "dest_lon": None,
            "rota_coords": [],
            "pracas": [],
            "fonte_rota": "Sem destino",
        }

    if normalizar_texto(cidade_limpa) == "piracicaba" and not bairro_limpo:
        return {
            "km_ida": 0.0,
            "qtd_pedagios": 0,
            "valor_pedagio_un": 0.0,
            "pedagio_ida_volta": 0.0,
            "dest_lat": BASE_LAT,
            "dest_lon": BASE_LON,
            "rota_coords": [],
            "pracas": [],
            "fonte_rota": "Base local Piracicaba",
        }

    if normalizar_texto(cidade_limpa) == "piracicaba" and bairro_limpo:
        destino_query = f"{bairro_limpo}, Piracicaba, {uf_limpa}, Brasil"
    else:
        destino_query = f"{cidade_limpa}, {uf_limpa}, Brasil"

    km_ida = 0.0
    dest_lat = None
    dest_lon = None
    rota_coords: list[list[float]] = []
    fonte_rota = "Estimativa local"

    if HAS_REQUESTS and requests:
        try:
            r_geo = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": destino_query, "format": "json", "limit": 1},
                headers={"User-Agent": "Mozilla/5.0 AIC_Calculadora_Web/1.2"},
                timeout=8,
            )
            dados_geo = r_geo.json()
            if dados_geo:
                dest_lat = float(dados_geo[0]["lat"])
                dest_lon = float(dados_geo[0]["lon"])

                osrm_url = (
                    f"http://router.project-osrm.org/route/v1/driving/"
                    f"{BASE_LON},{BASE_LAT};{dest_lon},{dest_lat}"
                    f"?overview=full&geometries=geojson"
                )
                r_rota = requests.get(
                    osrm_url,
                    headers={"User-Agent": "Mozilla/5.0 AIC_Calculadora_Web/1.2"},
                    timeout=8,
                )
                dados_rota = r_rota.json()
                if dados_rota.get("code") == "Ok":
                    rota = dados_rota["routes"][0]
                    km_ida = round(rota["distance"] / 1000.0, 1)
                    coords_lon_lat = rota.get("geometry", {}).get("coordinates", [])
                    rota_coords = [[c[1], c[0]] for c in coords_lon_lat[::3]]
                    fonte_rota = "OpenStreetMap / OSRM"
        except Exception:
            pass

    pracas_detectadas: list[str] = []
    if normalizar_texto(cidade_limpa) != "piracicaba" and km_ida > 15 and rota_coords:
        for nome_praca, plat, plon, tarifa in PRACAS_PEDAGIO_SP:
            menor_dist = min(distancia_km(plat, plon, rlat, rlon) for rlat, rlon in rota_coords)
            if menor_dist <= 8.5:
                pracas_detectadas.append(f"{nome_praca} (R$ {tarifa:.2f})")

    if pracas_detectadas:
        tarifas = [float(re.search(r"R\$\s*([0-9.]+)", p).group(1)) for p in pracas_detectadas]
        qtd_pedagios = len(tarifas)
        valor_pedagio_un = round(sum(tarifas) / qtd_pedagios, 2)
        pedagio_ida_volta = round(sum(tarifas) * 2.0, 2)
    elif normalizar_texto(cidade_limpa) != "piracicaba" and km_ida > 15:
        qtd_pedagios = max(1, int(km_ida // 65))
        valor_pedagio_un = 13.50
        pedagio_ida_volta = round(qtd_pedagios * valor_pedagio_un * 2.0, 2)
    else:
        qtd_pedagios = 0
        valor_pedagio_un = 0.0
        pedagio_ida_volta = 0.0

    return {
        "km_ida": km_ida,
        "qtd_pedagios": qtd_pedagios,
        "valor_pedagio_un": valor_pedagio_un,
        "pedagio_ida_volta": pedagio_ida_volta,
        "dest_lat": dest_lat,
        "dest_lon": dest_lon,
        "rota_coords": rota_coords,
        "pracas": pracas_detectadas,
        "fonte_rota": fonte_rota,
    }


def _num(valor, padrao: float) -> float:
    if valor is None or str(valor).strip() == "":
        return padrao
    return float(str(valor).replace(",", "."))


def calcular_orcamento_completo(dados: dict) -> dict:
    cidade = (dados.get("cidade") or "Piracicaba").strip() or "Não informada"
    uf = (dados.get("uf") or "SP").strip().upper()
    bairro = (dados.get("bairro") or "").strip()

    fora_de_sp = bool(uf) and uf != "SP"
    dist_ida = _num(dados.get("distancia_ida"), 0.0)
    preco_comb = _num(dados.get("preco_comb"), 6.09)
    consumo = _num(dados.get("consumo"), 9.1)
    qtd_pedagios = int(_num(dados.get("qtd_pedagios"), 0.0))
    valor_pedagio_un = _num(dados.get("valor_pedagio_un"), 0.0)
    pedagio_ida_volta = _num(dados.get("pedagio_ida_volta"), round(qtd_pedagios * valor_pedagio_un * 2.0, 2))
    horas_dia = _num(dados.get("horas_dia"), 8.0)
    valor_hora = 200.0 if fora_de_sp else _num(dados.get("valor_hora"), 100.0)
    dias = max(1, int(_num(dados.get("dias"), 5.0)))
    periculosidade_dia = _num(dados.get("periculosidade_dia"), periculosidade_por_cidade(cidade))
    almoco_dia = _num(dados.get("almoco_dia"), 45.0)
    hotel_diaria = 160.0 if dist_ida > 100.0 else _num(dados.get("hotel_diaria"), 160.0)
    passagem_aerea = _num(dados.get("passagem_aerea"), PASSAGENS_REFERENCIA_SP.get(uf, 1350.0)) if fora_de_sp else 0.0

    if consumo <= 0:
        return {"erro": "O consumo do veículo deve ser maior que zero."}

    distancia_indefinida = normalizar_texto(cidade) == "piracicaba" and not bairro and dist_ida <= 0.0
    precisa_hotel = dist_ida > 100.0

    valor_diaria_tecnica = horas_dia * valor_hora
    total_horas = horas_dia * dias
    custo_honorarios = valor_diaria_tecnica * dias
    custo_periculosidade = dias * periculosidade_dia

    if precisa_hotel:
        dist_total = dist_ida * 2.0
        custo_pedagio_total = pedagio_ida_volta
        qtd_diarias_hotel = dias
        custo_hotel = qtd_diarias_hotel * hotel_diaria
        modo_viagem = f"Permanência na cidade por {dias} dia(s) (>100 km: hotel R$ 160/dia, sem ida/volta diária)"
    else:
        dist_total = (dist_ida * 2.0) * dias
        custo_pedagio_total = pedagio_ida_volta * dias
        qtd_diarias_hotel = 0
        custo_hotel = 0.0
        modo_viagem = f"Deslocamento diário ({dias}x ida e volta)"

    if distancia_indefinida:
        litros_necessarios = 0.0
        custo_combustivel = 0.0
    else:
        litros_necessarios = dist_total / consumo
        custo_combustivel = litros_necessarios * preco_comb

    custo_alimentacao = dias * almoco_dia

    total_geral = (
        custo_honorarios
        + custo_periculosidade
        + custo_combustivel
        + custo_pedagio_total
        + custo_alimentacao
        + custo_hotel
        + passagem_aerea
    )
    sinal_50 = total_geral * 0.50

    destino_str = f"{cidade} - {uf}"
    if normalizar_texto(cidade) == "piracicaba":
        destino_str += f" (Bairro: {bairro})" if bairro else " (Bairro não definido)"

    linhas = [
        "=" * 64,
        "ORÇAMENTO OPERACIONAL — A.I.C (AGÊNCIA DE INVESTIGAÇÃO CHARLIE)",
        f"Data/Hora : {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "=" * 64,
        f"ORIGEM : {ORIGEM_ORCAMENTO_LABEL}",
        f"DESTINO : {destino_str}",
    ]
    if fora_de_sp:
        linhas.append(f"TARIFA UF : Fora de SP ({uf}) -> R$ 200,00/hora aplicado")
    if distancia_indefinida:
        linhas.append("DISTÂNCIA : Não definido | Total rodado: Não definido")
    else:
        linhas.append(f"DISTÂNCIA : {dist_ida:.1f} km (ida) | Total rodado: {dist_total:.1f} km")

    linhas.extend([
        f"LOGÍSTICA : {modo_viagem}",
        f"CARGA : {dias} dia(s) x {horas_dia:.1f}h/dia = {total_horas:.1f} horas totais",
        "",
        "--- DETALHAMENTO ---",
        f"1. Diárias Técnicas ({dias}d x R$ {valor_diaria_tecnica:.2f}/dia) : R$ {custo_honorarios:>9.2f}",
        f"2. Periculosidade ({dias}d x R$ {periculosidade_dia:.2f}/dia) : R$ {custo_periculosidade:>9.2f}",
    ])

    if distancia_indefinida:
        linhas.append("3. Combustível (Distância base não definida) : R$ 0.00")
    else:
        linhas.append(f"3. Combustível ({litros_necessarios:.1f}L @ {consumo:.1f}km/L x R$ {preco_comb:.2f}) : R$ {custo_combustivel:>9.2f}")

    linhas.extend([
        f"4. Pedágios ({qtd_pedagios} praça(s)/ida | ~R$ {valor_pedagio_un:.2f}/un) : R$ {custo_pedagio_total:>9.2f}",
        f"5. Alimentação/Almoço ({dias}d x R$ {almoco_dia:.2f}/dia) : R$ {custo_alimentacao:>9.2f}",
    ])

    if precisa_hotel:
        linhas.append(f"6. Hospedagem Hotel (>100km: {qtd_diarias_hotel}x R$ {hotel_diaria:.2f}) : R$ {custo_hotel:>9.2f}")
    else:
        linhas.append("6. Hospedagem Hotel (<=100km - Isento) : R$ 0.00")

    if fora_de_sp:
        linhas.append(f"7. Passagem Aérea Ida+Volta (Fora SP - {uf}) : R$ {passagem_aerea:>9.2f}")
    else:
        linhas.append("7. Passagem Aérea Ida+Volta (Estado de SP) : R$ 0.00")

    linhas.extend([
        "-" * 64,
        f"VALOR TOTAL DO SERVIÇO : R$ {total_geral:>9.2f}",
        f"CONDIÇÃO PADRÃO (50% Entrada / 50% Entrega) : R$ {sinal_50:>9.2f} + R$ {sinal_50:>9.2f}",
        "-" * 64,
    ])

    return {
        "total": round(total_geral, 2),
        "sinal_50": round(sinal_50, 2),
        "periculosidade_dia": periculosidade_dia,
        "valor_hora": valor_hora,
        "hotel_diaria": hotel_diaria if precisa_hotel else 0.0,
        "texto": "\n".join(linhas),
    }


HTML_LOGIN = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>A.I.C — Acesso Remoto</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 16px;
      background: #0b0f14;
      color: #e0f2fe;
      font-family: Consolas, monospace;
    }
    .login-card {
      width: 100%;
      max-width: 380px;
      background: #111822;
      border: 1px solid #1e3a4c;
      border-radius: 8px;
      padding: 20px;
    }
    h1 { margin: 0 0 8px; color: #00e5ff; font-size: 1.05rem; }
    p { margin: 0 0 14px; color: #7f9eb2; font-size: 0.82rem; }
    label { display: block; font-size: 0.8rem; color: #7f9eb2; margin-bottom: 4px; }
    input {
      width: 100%;
      padding: 9px 10px;
      margin-bottom: 12px;
      background: #16202c;
      color: #00e5ff;
      border: 1px solid #1e3a4c;
      border-radius: 4px;
      font-family: inherit;
    }
    button {
      width: 100%;
      padding: 10px;
      border: none;
      border-radius: 5px;
      background: #00ff88;
      color: #081016;
      font-family: inherit;
      font-weight: bold;
      cursor: pointer;
    }
    .erro { color: #ff6b6b; font-size: 0.82rem; margin-bottom: 10px; }
  </style>
</head>
<body>
  <form class="login-card" method="POST" action="/login">
    <h1>◈ A.I.C — ACESSO EXTERNO</h1>
    <p>Dispositivo fora do servidor principal: informe usuário e senha para entrar.</p>
    __ERRO_HTML__
    <label>Usuário</label>
    <input type="text" name="usuario" autocomplete="username" required />
    <label>Senha</label>
    <input type="password" name="senha" autocomplete="current-password" required />
    <button type="submit">ENTRAR</button>
  </form>
</body>
</html>
"""


HTML_CALCULADORA = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>A.I.C — Calculadora Operacional (Web & Mobile)</title>
  <style>
    :root {
      --bg: #0b0f14;
      --panel: #111822;
      --input: #16202c;
      --cyan: #00e5ff;
      --green: #00ff88;
      --amber: #ffaa00;
      --text: #e0f2fe;
      --muted: #7f9eb2;
      --border: #1e3a4c;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 14px;
      background: var(--bg);
      color: var(--text);
      font-family: Consolas, monospace;
    }
    .container {
      max-width: 1120px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 1fr 1.15fr;
      gap: 14px;
    }
    @media (max-width: 860px) {
      .container { grid-template-columns: 1fr; }
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      margin-bottom: 14px;
    }
    h1, h2 {
      margin: 0 0 10px 0;
      color: var(--cyan);
      font-size: 1rem;
    }
    .qr-box {
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      align-items: center;
    }
    .qr-box img {
      width: 156px;
      height: 156px;
      background: #ffffff;
      padding: 6px;
      border-radius: 6px;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px 12px;
    }
    @media (max-width: 540px) {
      .grid-2 { grid-template-columns: 1fr; }
    }
    label {
      display: block;
      font-size: 0.8rem;
      color: var(--muted);
      margin-bottom: 3px;
    }
    input {
      width: 100%;
      padding: 7px 9px;
      background: var(--input);
      color: var(--cyan);
      border: 1px solid var(--border);
      border-radius: 4px;
      font-family: inherit;
      font-size: 0.9rem;
    }
    .btn-row {
      display: flex;
      gap: 8px;
      margin-top: 10px;
      flex-wrap: wrap;
    }
    button {
      flex: 1;
      min-width: 150px;
      padding: 10px;
      border: none;
      border-radius: 5px;
      font-family: inherit;
      font-weight: bold;
      cursor: pointer;
    }
    .btn-cyan { background: #0d2b3e; color: var(--cyan); border: 1px solid var(--cyan); }
    .btn-green { background: var(--green); color: #081016; }
    .btn-amber { background: #2a1919; color: var(--amber); border: 1px solid var(--amber); }
    .btn-wpp { background: #128c7e; color: #ffffff; border: 1px solid #25d366; }
    pre {
      background: #080c10;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      color: var(--green);
      white-space: pre-wrap;
      min-height: 340px;
      font-size: 0.86rem;
      margin: 0;
    }
    .muted { color: var(--muted); font-size: 0.82rem; }
  </style>
</head>
<body>
  <div class="card" style="max-width:1120px;margin:0 auto 14px;">
    <h1>◈ A.I.C — CALCULADORA OPERACIONAL & LOGÍSTICA (WEB + CELULAR)</h1>
    <div class="qr-box">
      <img src="/qrcode.png" alt="QR Code para acessar no celular" onerror="this.style.display='none'" />
      <div>
        <div><strong>Acesso no celular / outro dispositivo:</strong></div>
        <div style="margin:6px 0;"><a href="__MOBILE_URL__" style="color:#00e5ff;">__MOBILE_URL__</a></div>
        <div class="muted" id="status_web">Origem no orçamento: Piracicaba - SP • Login externo ativo</div>
      </div>
    </div>
  </div>

  <div class="container">
    <div class="card">
      <h2>[ PARÂMETROS & CUSTOS ]</h2>
      <div class="grid-2">
        <div><label>Cidade de Destino</label><input id="cidade" value="Piracicaba" /></div>
        <div><label>Estado (UF)</label><input id="uf" value="SP" maxlength="2" /></div>
        <div><label>Bairro (se Piracicaba)</label><input id="bairro" value="Nova América" /></div>
        <div><label>Distância Ida (km)</label><input id="distancia_ida" value="0.0" /></div>
        <div><label>Preço Combustível (R$/L)</label><input id="preco_comb" value="6.09" /></div>
        <div><label>Consumo Veículo (km/L)</label><input id="consumo" value="9.1" /></div>
        <div><label>Qtd. Pedágios (Ida)</label><input id="qtd_pedagios" value="0" /></div>
        <div><label>Valor Médio Pedágio (R$)</label><input id="valor_pedagio_un" value="0.00" /></div>
        <div><label>Total Pedágios Ida+Volta (R$)</label><input id="pedagio_ida_volta" value="0.00" /></div>
        <div><label>Horas por Diária</label><input id="horas_dia" value="8" /></div>
        <div><label>Valor Hora Técnica (R$)</label><input id="valor_hora" value="100.00" /></div>
        <div><label>Quantidade de Dias</label><input id="dias" value="5" /></div>
        <div><label>Periculosidade / Dia (R$)</label><input id="periculosidade_dia" value="50.00" /></div>
        <div><label>Alimentação / Dia (R$)</label><input id="almoco_dia" value="45.00" /></div>
        <div><label>Diária Hotel (&gt;100 km) (R$)</label><input id="hotel_diaria" value="160.00" /></div>
        <div><label>Passagem Aérea I+V (Fora SP)</label><input id="passagem_aerea" value="0.00" /></div>
        <div><label>WhatsApp Destino (opcional, só DDD+Nº)</label><input id="wpp_telefone" placeholder="Ex: 19999999999" /></div>
      </div>

      <div class="btn-row">
        <button class="btn-cyan" onclick="atualizarRota()">🌐 ATUALIZAR ROTA & CUSTOS WEB</button>
        <button class="btn-amber" onclick="limparCampos()">🧹 LIMPAR</button>
      </div>
      <div class="btn-row">
        <button class="btn-green" onclick="calcular()">⚡ CALCULAR ORÇAMENTO FINAL</button>
      </div>
    </div>

    <div class="card">
      <h2>[ ORÇAMENTO GERADO ]</h2>
      <pre id="saida">Preencha os campos e clique em [CALCULAR ORÇAMENTO FINAL].</pre>
      <div class="btn-row">
        <button class="btn-cyan" onclick="copiarSaida()">📋 COPIAR ORÇAMENTO</button>
        <button class="btn-wpp" onclick="enviarWhatsApp()">💬 ENVIAR ORÇAMENTO VIA WHATSAPP</button>
      </div>
    </div>
  </div>

  <script>
    function val(id) { return document.getElementById(id).value; }
    function setVal(id, v) { document.getElementById(id).value = v; }

    function payloadAtual() {
      return {
        cidade: val('cidade'),
        uf: val('uf'),
        bairro: val('bairro'),
        distancia_ida: val('distancia_ida'),
        preco_comb: val('preco_comb'),
        consumo: val('consumo'),
        qtd_pedagios: val('qtd_pedagios'),
        valor_pedagio_un: val('valor_pedagio_un'),
        pedagio_ida_volta: val('pedagio_ida_volta'),
        horas_dia: val('horas_dia'),
        valor_hora: val('valor_hora'),
        dias: val('dias'),
        periculosidade_dia: val('periculosidade_dia'),
        almoco_dia: val('almoco_dia'),
        hotel_diaria: val('hotel_diaria'),
        passagem_aerea: val('passagem_aerea')
      };
    }

    async function atualizarRota() {
      document.getElementById('status_web').textContent = 'Consultando rota, pedágios, combustível e passagens...';
      const r = await fetch('/api/rota', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ cidade: val('cidade'), uf: val('uf'), bairro: val('bairro') })
      });
      if (r.status === 401) { window.location.reload(); return; }
      const d = await r.json();
      setVal('distancia_ida', Number(d.km_ida || 0).toFixed(1));
      setVal('qtd_pedagios', d.qtd_pedagios || 0);
      setVal('valor_pedagio_un', Number(d.valor_pedagio_un || 0).toFixed(2));
      setVal('pedagio_ida_volta', Number(d.pedagio_ida_volta || 0).toFixed(2));
      setVal('preco_comb', Number(d.preco_comb || 6.09).toFixed(2));
      setVal('passagem_aerea', Number(d.passagem_aerea || 0).toFixed(2));
      setVal('valor_hora', Number(d.valor_hora || 100).toFixed(2));
      setVal('periculosidade_dia', Number(d.periculosidade_dia || 100).toFixed(2));
      if (Number(d.km_ida || 0) > 100) setVal('hotel_diaria', '160.00');
      document.getElementById('status_web').textContent = 'Atualizado via ' + (d.fonte_rota || 'referência local');
      await calcular();
    }

    async function calcular() {
      const r = await fetch('/api/calcular', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payloadAtual())
      });
      if (r.status === 401) { window.location.reload(); return; }
      const d = await r.json();
      if (d.erro) {
        document.getElementById('saida').textContent = 'Erro: ' + d.erro;
        return;
      }
      setVal('valor_hora', Number(d.valor_hora).toFixed(2));
      setVal('periculosidade_dia', Number(d.periculosidade_dia).toFixed(2));
      document.getElementById('saida').textContent = d.texto;
    }

    function limparCampos() {
      setVal('cidade', '');
      setVal('uf', 'SP');
      setVal('bairro', '');
      setVal('distancia_ida', '0.0');
      setVal('qtd_pedagios', '0');
      setVal('valor_pedagio_un', '0.00');
      setVal('pedagio_ida_volta', '0.00');
      setVal('valor_hora', '100.00');
      setVal('periculosidade_dia', '100.00');
      setVal('hotel_diaria', '160.00');
      setVal('passagem_aerea', '0.00');
      document.getElementById('saida').textContent = 'Campos limpos.';
    }

    function copiarSaida() {
      const txt = document.getElementById('saida').textContent;
      if (navigator.clipboard) navigator.clipboard.writeText(txt);
    }

    function enviarWhatsApp() {
      const txt = (document.getElementById('saida').textContent || '').trim();
      if (!txt) return;
      let fone = val('wpp_telefone').replace(/\D/g, '');
      if (fone.length === 10 || fone.length === 11) {
        fone = '55' + fone;
      }
      const baseUrl = fone ? ('https://wa.me/' + fone) : 'https://wa.me/';
      const link = baseUrl + '?text=' + encodeURIComponent(txt);
      window.open(link, '_blank', 'noopener,noreferrer');
    }

    calcular();
  </script>
</body>
</html>
"""


def iniciar_calculadora_web(porta: int = 8799, abrir_navegador: bool = True) -> None:
    ip_local = obter_ip_local()
    url_local = f"http://127.0.0.1:{porta}/"
    url_mobile = f"http://{ip_local}:{porta}/"
    qr_png = gerar_qrcode_png_bytes(url_mobile)

    class Handler(BaseHTTPRequestHandler):
        def _eh_servidor_principal(self) -> bool:
            ip_cliente = (self.client_address[0] if self.client_address else "").strip()
            return ip_cliente in {"127.0.0.1", "::1"}

        def _esta_autenticado(self) -> bool:
            if self._eh_servidor_principal():
                return True
            raw_cookie = self.headers.get("Cookie", "")
            if not raw_cookie:
                return False
            cookie = SimpleCookie()
            try:
                cookie.load(raw_cookie)
            except Exception:
                return False
            morsel = cookie.get(NOME_COOKIE_SESSAO)
            return bool(morsel and _token_sessao_valido(morsel.value))

        def _enviar_html(self, codigo: int, html_str: str, cookie_header: str | None = None) -> None:
            raw = html_str.encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            if cookie_header:
                self.send_header("Set-Cookie", cookie_header)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _enviar_login(self, erro_msg: str = "") -> None:
            erro_html = f'<div class="erro">{erro_msg}</div>' if erro_msg else ""
            pagina = HTML_LOGIN.replace("__ERRO_HTML__", erro_html)
            self._enviar_html(200, pagina)

        def _json(self, status: int, payload: dict) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            rota = urllib.parse.urlparse(self.path).path
            if not self._esta_autenticado():
                self._enviar_login()
                return

            if rota == "/qrcode.png" and qr_png:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(qr_png)))
                self.end_headers()
                self.wfile.write(qr_png)
                return

            html = HTML_CALCULADORA.replace("__MOBILE_URL__", url_mobile)
            self._enviar_html(200, html)

        def do_POST(self) -> None:
            tamanho = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(tamanho) if tamanho > 0 else b"{}"
            rota = urllib.parse.urlparse(self.path).path

            if rota == "/login":
                content_type = self.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    try:
                        form = json.loads(raw.decode("utf-8", errors="ignore") or "{}")
                    except Exception:
                        form = {}
                    usuario = str(form.get("usuario") or "").strip()
                    senha = str(form.get("senha") or "").strip()
                else:
                    parsed = urllib.parse.parse_qs(raw.decode("utf-8", errors="ignore"))
                    usuario = (parsed.get("usuario", [""])[0] or "").strip()
                    senha = (parsed.get("senha", [""])[0] or "").strip()

                if hmac.compare_digest(usuario, USUARIO_REMOTO) and hmac.compare_digest(senha, SENHA_REMOTA):
                    token = _gerar_token_sessao(usuario)
                    cookie_val = f"{NOME_COOKIE_SESSAO}={token}; Path=/; HttpOnly; SameSite=Lax"
                    self.send_response(302)
                    self.send_header("Set-Cookie", cookie_val)
                    self.send_header("Location", "/")
                    self.end_headers()
                    return

                self._enviar_login("Usuário ou senha inválidos.")
                return

            if not self._esta_autenticado():
                self._json(401, {"erro": "Sessão não autenticada. Faça login para acessar."})
                return

            try:
                dados = json.loads(raw.decode("utf-8", errors="ignore") or "{}")
            except Exception:
                dados = {}

            if rota == "/api/rota":
                cidade = (dados.get("cidade") or "").strip()
                uf = (dados.get("uf") or "SP").strip().upper()
                bairro = (dados.get("bairro") or "").strip()
                info_rota = estimar_rota_e_pedagios(cidade, uf, bairro)
                preco_comb, _ = buscar_combustivel_sp()
                passagem, _ = buscar_passagem_referencia(cidade, uf)
                info_rota["preco_comb"] = preco_comb
                info_rota["passagem_aerea"] = passagem
                info_rota["valor_hora"] = 200.0 if (uf and uf != "SP") else 100.0
                info_rota["periculosidade_dia"] = periculosidade_por_cidade(cidade)
                self._json(200, info_rota)
            elif rota == "/api/calcular":
                self._json(200, calcular_orcamento_completo(dados))
            else:
                self._json(404, {"erro": "Rota não encontrada"})

        def log_message(self, format, *args) -> None:
            return

    servidor = ThreadingHTTPServer(("0.0.0.0", porta), Handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    print(f"Calculadora aberta no navegador: {url_local}")
    print(f"Acesso externo / celular: {url_mobile} (Login: {USUARIO_REMOTO} / {SENHA_REMOTA})")
    print(gerar_qrcode_ascii(url_mobile))

    if abrir_navegador:
        webbrowser.open_new_tab(url_local)

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        servidor.shutdown()


if __name__ == "__main__":
    iniciar_calculadora_web(porta=8799, abrir_navegador=True)