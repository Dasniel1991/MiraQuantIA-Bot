import ccxt
import time
import requests
import os
import json
from datetime import datetime
from google import genai

# ==========================================
# 1. CONFIGURAÇÕES E ENDPOINTS
# ==========================================
BASE44_API_KEY = os.environ.get("BASE44_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

cliente_ia = genai.Client(api_key=GEMINI_API_KEY)
MODELO_GEMINI = "gemini-3-flash-preview"

BASE_URL = "https://miraquant-ia.base44.app/api"
ENDPOINTS = {
    "controle": f"{BASE_URL}/entities/ControleBot",
    "operacao": f"{BASE_URL}/entities/Operacao",
    "saldo": f"{BASE_URL}/entities/SaldoUsuario"
}

# 🚀 AGORA O ROBÔ VIGIA MÚLTIPLAS MOEDAS AO MESMO TEMPO
MOEDAS_ATIVAS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

# Memória do Robô (Agora organizada por Usuário + Moeda)
operacoes_abertas = {} 
ordens_fantasma = {}
historico_hora = {}
ultima_reuniao_ia = {}
data_operacao_usuario = {}

# ==========================================
# 2. FUNÇÕES DE APOIO E LEITURA DE MERCADO
# ==========================================
def api_base44(metodo, endpoint, dados=None, id_registro=None):
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    url = f"{endpoint}/{id_registro}" if id_registro else endpoint
    try:
        if metodo == "GET": res = requests.get(url, headers=headers)
        elif metodo == "POST": res = requests.post(url, json=dados, headers=headers)
        elif metodo == "PUT": res = requests.put(url, json=dados, headers=headers)
        if res.status_code in [200, 201, 204]: return res.json() if res.text else True
        return None
    except: return None

def obter_medo_e_ganancia():
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        dados = res.json()
        valor = int(dados['data'][0]['value'])
        classificacao = dados['data'][0]['value_classification']
        return f"{valor} ({classificacao})"
    except:
        return "Neutro"

def obter_radar_baleias(symbol="BTCUSDT"):
    try:
        # Remove a barra para a API da Binance (ex: BTC/USDT -> BTCUSDT)
        simbolo_binance = symbol.replace("/", "")
        url = f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={simbolo_binance}&period=5m"
        res = requests.get(url, timeout=5)
        dados = res.json()
        if dados and len(dados) > 0:
            ultimo = dados[-1]
            longs_pct = float(ultimo['longAccount']) * 100
            shorts_pct = float(ultimo['shortAccount']) * 100
            return f"{longs_pct:.1f}% Comprados (Long) vs {shorts_pct:.1f}% Vendidos (Short)"
        return "Dados Indisponíveis"
    except Exception as e:
        return "Dados Indisponíveis"

def ler_mercado(exchange, symbol):
    try:
        velas = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=15)
        fechamentos = [v[4] for v in velas]
        maximas = [v[2] for v in velas]
        minimas = [v[3] for v in velas]
        preco_atual = fechamentos[-1]
        
        volatilidade = ((max(maximas) - min(minimas)) / min(minimas)) * 100

        ganhos, perdas = [], []
        for i in range(1, len(fechamentos)):
            diff = fechamentos[i] - fechamentos[i-1]
            if diff > 0: ganhos.append(diff)
            else: perdas.append(abs(diff))
        media_ganhos = sum(ganhos) / 14 if ganhos else 0
        media_perdas = sum(perdas) / 14 if perdas else 0
        rsi = 100 if media_perdas == 0 else 100 - (100 / (1 + (media_ganhos / media_perdas)))
        
        try:
            order_book = exchange.fetch_order_book(symbol, limit=100)
            compras_m = sum(bid[0] * bid[1] for bid in order_book['bids']) / 1_000_000
            vendas_m = sum(ask[0] * ask[1] for ask in order_book['asks']) / 1_000_000
            raio_x_book = f"${compras_m:.2f}M Compras vs ${vendas_m:.2f}M Vendas"
        except: raio_x_book = "Indisponível"

        try:
            velas_1h = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=200)
            fechamentos_1h = [v[4] for v in velas_1h]
            media_200 = sum(fechamentos_1h) / len(fechamentos_1h)
            tendencia_macro = "ALTA" if preco_atual > media_200 else "BAIXA"
        except: tendencia_macro = "Indisponível"

        try:
            funding = exchange.fetch_funding_rate(symbol)
            taxa_funding = funding['fundingRate'] * 100 
        except: taxa_funding = 0.0
            
        return preco_atual, rsi, volatilidade, raio_x_book, tendencia_macro, taxa_funding
    except Exception as e:
        print(f"Erro na leitura do mercado ({symbol}): {e}")
        return None, None, None, None, None, None

def atualizar_dashboard_total(usuario, lucro_operacao_pct, valor_financeiro):
    uid = usuario['usuario_id']
    saldos = api_base44("GET", ENDPOINTS["saldo"])
    if saldos:
        reg_saldo = next((s for s in saldos if s['usuario_id'] == uid), None)
        if reg_saldo:
            novo_saldo = float(reg_saldo.get('saldo_demo', 0)) + valor_financeiro
            api_base44("PUT", ENDPOINTS["saldo"], {"saldo_demo": novo_saldo}, id_registro=reg_saldo['id'])

    lucro_acumulado = float(usuario.get("lucro_hoje_porcentagem") or 0.0) + lucro_operacao_pct
    api_base44("PUT", ENDPOINTS["controle"], {
        "lucro_hoje_porcentagem": lucro_acumulado,
        "lucro_hoje": 100 * (lucro_acumulado / 100) 
    }, id_registro=usuario['id'])

def recuperar_memoria_encravada():
    print("🔍 Procurando por ordens abertas na base de dados...")
    todas_ops = api_base44("GET", ENDPOINTS["operacao"])
    if todas_ops:
        for op in todas_ops:
            if op.get("status") == "Aberta":
                uid = op.get("usuario_id")
                symbol = op.get("par_moeda", "BTC/USDT")
                mem_key = f"{uid}_{symbol}"
                cat = op.get("categoria_ordem", "Demo")
                preco_ent = float(op.get("preco_entrada", 0))
                if preco_ent == 0: continue
                tipo = op.get("tipo_ordem", "Compra")

                if cat == "Fantasma":
                    if mem_key not in ordens_fantasma: ordens_fantasma[mem_key] = []
                    alvo = preco_ent * 1.004 if tipo == "Compra" else preco_ent * 0.996
                    stop = preco_ent * 0.9965 if tipo == "Compra" else preco_ent * 1.0035
                    ordens_fantasma[mem_key].append({
                        "id": op['id'], "entrada": preco_ent, "alvo": alvo, "stop": stop, 
                        "tipo_ordem": tipo, "hora_criacao": time.time(), "capital_alocado": 100.0
                    })
                else:
                    if mem_key not in operacoes_abertas:
                        operacoes_abertas[mem_key] = {
                            "id": op['id'], "entrada": preco_ent, "lucro_maximo": 0.0, 
                            "trailing_ativo": False, "capital_alocado": float(op.get("lucro_financeiro", 100.0)), "tipo_ordem": tipo
                        }
        print("✅ Memória restaurada com sucesso!")

# ==========================================
# 3. O CÉREBRO: IA + RADAR MULTIPAR
# ==========================================
def reuniao_com_ia_gestora(usuario, symbol, preco_atual, rsi_atual, volatilidade, marcha, raio_x_book, tendencia_macro, taxa_funding, indice_medo, radar_baleias):
    id_banco = usuario.get("id") 
    mem_key = f"{usuario.get('usuario_id')}_{symbol}"
    
    rsi_antigo = usuario.get("rsi_alvo", 40)
    direcao_antiga = usuario.get("direcao_operacao", "Compra")
    hist = historico_hora.get(mem_key, {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0})
    
    print(f"\n🧠 [REUNIÃO IA] Analisando {symbol}...")
    prompt = f"""
    Ativo: {symbol}. Preço: {preco_atual:.2f}. RSI: {rsi_atual:.2f}. Direção Anterior: {direcao_antiga} | RSI Alvo: {rsi_antigo}.
    
    DADOS INSTITUCIONAIS:
    1. Volatilidade: {volatilidade:.2f}% (Marcha {marcha})
    2. EMA 200: Tendência de {tendencia_macro}.
    3. Order Book: {raio_x_book}.
    4. Funding Rate: {taxa_funding:.4f}%.
    5. Sentimento Global: {indice_medo}.
    6. RADAR DE BALEIAS: {radar_baleias}.
    
    PLACAR NESTA MOEDA:
    Reais - Vitórias: {hist['reais_vitorias']} | Derrotas: {hist['reais_derrotas']}
    Fantasmas - Vitórias: {hist['fantasma_vitorias']} | Derrotas: {hist['fantasma_derrotas']}
    
    TAREFA COMO DIRETOR DE RISCO:
    - Escolha 'direcao_operacao' (Compra ou Venda) baseando-se na tendência e nas baleias.
    - Defina o 'rsi_alvo'.
    - INICIE O STATUS COM A MOEDA (Ex: "[{symbol}] 🟢 Seguindo tendência...").
    - Siga a matemática dos Fantasmas se as Baleias estiverem blefando.
    
    Responda APENAS um JSON:
    {{
      "direcao_operacao": "Compra",
      "rsi_alvo": 35, 
      "gatilho_trailing": 0.4, 
      "distancia_trailing": 0.2, 
      "status_mercado": "[{symbol}] 🟢 Fantasmas confirmam...",
      "observacao_ia": "Sua tese..."
    }}
    """
    try:
        res = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        nova_regra = json.loads(res.text.replace("```json", "").replace("```", "").strip())
        
        nova_regra["rsi_alvo_compra"] = nova_regra["rsi_alvo"] 
        api_base44("PUT", ENDPOINTS["controle"], nova_regra, id_registro=id_banco)
        print(f"✅ [NOVA DIRETRIZ {symbol}] Direção: {nova_regra['direcao_operacao']} | Alvo: {nova_regra['rsi_alvo']}")
        
        historico_hora[mem_key] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
    except Exception as e: print(f"❌ [ERRO IA {symbol}]: {e}")

# ==========================================
# 4. O OPERÁRIO: LOOP MULTIPAR
# ==========================================
def iniciar_loop():
    print("🚀 MIRAQUANTIA SCALPER - MODO MULTIPAR ATIVADO (BTC, ETH, SOL)")
    global ultima_reuniao_ia, ordens_fantasma, historico_hora, operacoes_abertas, data_operacao_usuario
    
    indice_medo = obter_medo_e_ganancia()
    ultimo_update_medo = time.time()
    
    recuperar_memoria_encravada()
    
    while True:
        try:
            if time.time() - ultimo_update_medo > 21600:
                indice_medo = obter_medo_e_ganancia()
                ultimo_update_medo = time.time()

            configs = api_base44("GET", ENDPOINTS["controle"])
            saldos = api_base44("GET", ENDPOINTS["saldo"]) 
            
            if not configs: time.sleep(60); continue

            ex = ccxt.bybit()
            dados_mercado = {}
            radar_baleias_dados = {}
            
            # 1. Coleta dados de TODAS as moedas ativas primeiro
            hora_atual = datetime.now().strftime('%H:%M:%S')
            hoje_data = datetime.now().strftime('%Y-%m-%d')
            print(f"\n[{hora_atual}] 🔭 ESCANEANDO O MERCADO...")
            
            for symbol in MOEDAS_ATIVAS:
                p, r, v, rb, tm, tf = ler_mercado(ex, symbol)
                if p is not None:
                    dados_mercado[symbol] = (p, r, v, rb, tm, tf)
                    radar_baleias_dados[symbol] = obter_radar_baleias(symbol)
                    print(f"   ► {symbol} | Preço: ${p:.2f} | RSI: {r:.2f} | Volatilidade: {v:.2f}%")

            # 2. Processa cada usuário
            for user in configs:
                uid = user.get("usuario_id")
                modo = str(user.get("modo_operacao", "Demo")).capitalize()
                meta = float(user.get("meta_diaria_porcentagem") or 2.0)
                limite_perda = float(user.get("risco_maximo_porcentagem") or 10.0)
                lucro_hoje = float(user.get("lucro_hoje_porcentagem") or 0.0)

                if not user.get("status_bot"): continue
                
                # RESET DA MEIA NOITE
                if uid not in data_operacao_usuario: data_operacao_usuario[uid] = hoje_data
                if data_operacao_usuario[uid] != hoje_data:
                    api_base44("PUT", ENDPOINTS["controle"], {"lucro_hoje_porcentagem": 0.0, "lucro_hoje": 0.0}, id_registro=user['id'])
                    data_operacao_usuario[uid] = hoje_data
                    lucro_hoje = 0.0

                if lucro_hoje >= meta: continue
                if lucro_hoje <= -limite_perda: continue

                # GESTÃO DO COFRE: Calcula Saldo Livre para não dar erro de margem
                reg_saldo = next((s for s in saldos if s['usuario_id'] == uid), None) if saldos else None
                saldo_demo_total = float(reg_saldo.get('saldo_demo', 100)) if reg_saldo else 100.0
                
                # Soma capital preso em todas as moedas deste usuário
                capital_preso = sum(op["capital_alocado"] for k, op in operacoes_abertas.items() if k.startswith(f"{uid}_"))
                saldo_livre = saldo_demo_total - capital_preso
                
                # Se não configurou capital fixo, usa 30% do saldo livre por moeda (para conseguir entrar em várias)
                capital_operacao_base = float(user.get("capital_por_operacao") or 0)
                if capital_operacao_base == 0:
                    capital_operacao = saldo_livre * 0.30 
                else:
                    capital_operacao = capital_operacao_base

                # 3. Varre as moedas
                for symbol in MOEDAS_ATIVAS:
                    if symbol not in dados_mercado: continue
                    preco, rsi, volatilidade, raio_x_book, tendencia_macro, taxa_funding = dados_mercado[symbol]
                    radar_baleias = radar_baleias_dados[symbol]
                    
                    mem_key = f"{uid}_{symbol}"
                    
                    if volatilidade < 0.5: tempo_espera_ia = 3600; marcha = "LENTA"
                    elif volatilidade <= 1.5: tempo_espera_ia = 1800; marcha = "NORMAL"
                    else: tempo_espera_ia = 900; marcha = "TURBO"

                    if mem_key not in ultima_reuniao_ia: ultima_reuniao_ia[mem_key] = 0
                    if mem_key not in ordens_fantasma: ordens_fantasma[mem_key] = []
                    if mem_key not in historico_hora: historico_hora[mem_key] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}

                    direcao = user.get("direcao_operacao", "Compra")
                    limite_rsi = float(user.get("rsi_alvo", user.get("rsi_alvo_compra", 40)))
                    gatilho_ts = float(user.get("gatilho_trailing", 0.4))
                    distancia_ts = float(user.get("distancia_trailing", 0.2))
                    stop_loss_rigido = -0.35

                    # REUNIÃO INDIVIDUAL POR MOEDA
                    if time.time() - ultima_reuniao_ia[mem_key] > tempo_espera_ia:
                        reuniao_com_ia_gestora(user, symbol, preco, rsi, volatilidade, marcha, raio_x_book, tendencia_macro, taxa_funding, indice_medo, radar_baleias)
                        ultima_reuniao_ia[mem_key] = time.time()
                        
                        # Recarrega regras que a IA acabou de mudar
                        configs_atualizadas = api_base44("GET", ENDPOINTS["controle"])
                        user = next((u for u in configs_atualizadas if u['usuario_id'] == uid), user)
                        direcao = user.get("direcao_operacao", "Compra")
                        limite_rsi = float(user.get("rsi_alvo", user.get("rsi_alvo_compra", 40)))

                    # GESTÃO DE ORDENS ABERTAS (REAL/DEMO)
                    if mem_key in operacoes_abertas:
                        op = operacoes_abertas[mem_key]
                        
                        lucro_pct = ((preco - op["entrada"]) / op["entrada"]) * 100 if op["tipo_ordem"] == "Compra" else ((op["entrada"] - preco) / op["entrada"]) * 100
                        if lucro_pct > op["lucro_maximo"]: op["lucro_maximo"] = lucro_pct

                        vender = False; motivo_venda = ""

                        if lucro_pct >= gatilho_ts and not op["trailing_ativo"]:
                            op["trailing_ativo"] = True
                            print(f"   🛡️ [{symbol}] TRAILING ATIVADO! Garantindo o lucro...")

                        if op["trailing_ativo"]:
                            linha_de_venda = op["lucro_maximo"] - distancia_ts
                            if lucro_pct <= linha_de_venda:
                                vender = True; motivo_venda = "Trailing Executado"
                        else:
                            if lucro_pct <= stop_loss_rigido:
                                vender = True; motivo_venda = "Stop Loss de Proteção"

                        if vender:
                            lucro_financeiro = op["capital_alocado"] * (lucro_pct / 100)
                            api_base44("PUT", ENDPOINTS["operacao"], {
                                "preco_saida": preco, "lucro_porcentagem": lucro_pct, 
                                "lucro_financeiro": lucro_financeiro, "status": "Fechada"
                            }, id_registro=op["id"])
                            atualizar_dashboard_total(user, lucro_pct, lucro_financeiro)
                            
                            if lucro_pct > 0: historico_hora[mem_key]['reais_vitorias'] += 1
                            else: historico_hora[mem_key]['reais_derrotas'] += 1
                            del operacoes_abertas[mem_key]

                    # PROCURA NOVAS ENTRADAS (SE TIVER SALDO LIVRE)
                    else:
                        sinal_compra = (direcao == "Compra" and rsi <= limite_rsi)
                        sinal_venda = (direcao == "Venda" and rsi >= limite_rsi)

                        if (sinal_compra or sinal_venda) and capital_operacao > 10: # Mínimo de 10 dólares pra operar
                            print(f"   🎯 [{symbol}] SINAL DE {direcao.upper()}! Alocando ${capital_operacao:.2f}")
                            res = api_base44("POST", ENDPOINTS["operacao"], {
                                "usuario_id": uid, "par_moeda": symbol, "tipo_ordem": direcao,
                                "categoria_ordem": modo, "preco_entrada": preco, "data_hora": datetime.now().isoformat(), "status": "Aberta"
                            })
                            if res and 'id' in res: 
                                operacoes_abertas[mem_key] = {
                                    "id": res['id'], "entrada": preco, "lucro_maximo": 0.0, 
                                    "trailing_ativo": False, "capital_alocado": capital_operacao, "tipo_ordem": direcao
                                }

                        # FANTASMAS (Sempre podem rodar, não custam saldo real)
                        elif ((direcao == "Compra" and rsi <= (limite_rsi + 15)) or (direcao == "Venda" and rsi >= (limite_rsi - 15))) and len(ordens_fantasma[mem_key]) < 2:
                            res = api_base44("POST", ENDPOINTS["operacao"], {
                                "usuario_id": uid, "par_moeda": symbol, "tipo_ordem": direcao,
                                "categoria_ordem": "Fantasma", "preco_entrada": preco, "data_hora": datetime.now().isoformat(), "status": "Aberta"
                            })
                            if res and 'id' in res:
                                alvo = preco * (1 + (gatilho_ts/100)) if direcao == "Compra" else preco * (1 - (gatilho_ts/100))
                                stop = preco * (1 + (stop_loss_rigido/100)) if direcao == "Compra" else preco * (1 - (stop_loss_rigido/100))
                                ordens_fantasma[mem_key].append({
                                    "id": res['id'], "entrada": preco, "alvo": alvo, "stop": stop, 
                                    "tipo_ordem": direcao, "hora_criacao": time.time(), "capital_alocado": 100.0
                                })

                    # ACOMPANHA FANTASMAS E FECHA-AS
                    for f in ordens_fantasma[mem_key][:]:
                        bateu_alvo = (f["tipo_ordem"] == "Compra" and preco >= f["alvo"]) or (f["tipo_ordem"] == "Venda" and preco <= f["alvo"])
                        bateu_stop = (f["tipo_ordem"] == "Compra" and preco <= f["stop"]) or (f["tipo_ordem"] == "Venda" and preco >= f["stop"])
                        timeout = (time.time() - f.get("hora_criacao", time.time())) > 2700

                        if bateu_alvo or bateu_stop or timeout:
                            lucro_pct = ((preco - f['entrada']) / f['entrada']) * 100 if f["tipo_ordem"] == "Compra" else ((f['entrada'] - preco) / f['entrada']) * 100
                                
                            if lucro_pct > 0: historico_hora[mem_key]['fantasma_vitorias'] += 1
                            else: historico_hora[mem_key]['fantasma_derrotas'] += 1
                            
                            lucro_financeiro = f.get("capital_alocado", 100) * (lucro_pct / 100)
                            
                            api_base44("PUT", ENDPOINTS["operacao"], {
                                "preco_saida": preco, "lucro_porcentagem": abs(lucro_pct),
                                "lucro_financeiro": lucro_financeiro, "status": "Fechada"
                            }, id_registro=f['id'])
                            ordens_fantasma[mem_key].remove(f)

            time.sleep(45) # Loop um pouco mais rápido agora
        except Exception as e: print(f"Erro Crítico: {e}"); time.sleep(60)

if __name__ == "__main__":
    iniciar_loop()
