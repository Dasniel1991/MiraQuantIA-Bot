import ccxt
import time
import requests
import os
import json
import traceback
from datetime import datetime, timedelta
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

# 🚀 ROBÔ MULTIMOEDAS
MOEDAS_ATIVAS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

# Memória do Robô
operacoes_abertas = {} 
ordens_fantasma = {}
historico_hora = {}
ultima_reuniao_ia = {}
data_operacao_usuario = {}

# Gestão do Relatório Triplo da IA
timestamps_ia = {'BTC/USDT': '--:--:--', 'ETH/USDT': '--:--:--', 'SOL/USDT': '--:--:--'}
teses_ia = {
    'BTC/USDT': 'Aguardando primeira leitura...', 
    'ETH/USDT': 'Aguardando primeira leitura...', 
    'SOL/USDT': 'Aguardando primeira leitura...'
}

# ==========================================
# 2. FUNÇÕES DE APOIO E LEITURA DE MERCADO
# ==========================================
def obter_data_hora_br():
    agora_utc = datetime.utcnow()
    return agora_utc - timedelta(hours=3)

def is_fim_de_semana():
    agora = obter_data_hora_br()
    dia_semana = agora.weekday() 
    hora = agora.hour
    if dia_semana == 5 or dia_semana == 6:
        return True
    if dia_semana == 4 and hora >= 20:
        return True
    return False

def api_base44(metodo, endpoint, dados=None, id_registro=None):
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    url = f"{endpoint}/{id_registro}" if id_registro else endpoint
    try:
        if metodo == "GET": res = requests.get(url, headers=headers, timeout=10)
        elif metodo == "POST": res = requests.post(url, json=dados, headers=headers, timeout=10)
        elif metodo == "PUT": res = requests.put(url, json=dados, headers=headers, timeout=10)
        if res.status_code in [200, 201, 204]: return res.json() if res.text else True
        return None
    except Exception as e:
        print(f"⚠️ Erro de comunicação com Base44 ({metodo}): {e}")
        return None

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
    except:
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
        return None, None, None, None, None, None

def reconciliar_lucro_diario(uid, id_banco, hoje_data, todas_ops_db):
    lucro_real = 0.0
    for op in todas_ops_db:
        if op.get("usuario_id") == uid and op.get("status") == "Fechada" and op.get("categoria_ordem") != "Fantasma":
            data_op = op.get("data_hora", "")
            if hoje_data in data_op:
                lucro_real += float(op.get("lucro_porcentagem") or 0.0) # Blindagem matemática
    
    api_base44("PUT", ENDPOINTS["controle"], {
        "lucro_hoje_porcentagem": lucro_real,
        "lucro_hoje": 100 * (lucro_real / 100) 
    }, id_registro=id_banco)
    return lucro_real

def atualizar_dashboard_total(usuario, lucro_operacao_pct, valor_financeiro):
    uid = usuario['usuario_id']
    saldos = api_base44("GET", ENDPOINTS["saldo"])
    if saldos:
        reg_saldo = next((s for s in saldos if s['usuario_id'] == uid), None)
        if reg_saldo:
            novo_saldo = float(reg_saldo.get('saldo_demo') or 0.0) + valor_financeiro # Blindagem
            api_base44("PUT", ENDPOINTS["saldo"], {"saldo_demo": novo_saldo}, id_registro=reg_saldo['id'])
            print(f"💰 Saldo Atualizado! {'+' if valor_financeiro >= 0 else ''}{valor_financeiro:.4f} USDT ajustados.")

    lucro_acumulado = float(usuario.get("lucro_hoje_porcentagem") or 0.0) + lucro_operacao_pct
    api_base44("PUT", ENDPOINTS["controle"], {
        "lucro_hoje_porcentagem": lucro_acumulado,
        "lucro_hoje": 100 * (lucro_acumulado / 100) 
    }, id_registro=usuario['id'])

def recuperar_memoria_encravada():
    print("🔍 Procurando por ordens abertas na base de dados para recuperar memória...")
    todas_ops = api_base44("GET", ENDPOINTS["operacao"])
    if todas_ops:
        for op in todas_ops:
            if op.get("status") == "Aberta":
                uid = op.get("usuario_id")
                symbol = op.get("par_moeda", "BTC/USDT")
                mem_key = f"{uid}_{symbol}"
                cat = op.get("categoria_ordem", "Demo")
                
                # 🛡️ BLINDAGEM MATEMÁTICA: Se o valor for None (vazio), assume o número padrão em vez de travar
                preco_ent = float(op.get("preco_entrada") or 0.0)
                if preco_ent == 0.0: continue
                
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
                        cap_alocado_seguro = float(op.get("capital_alocado") or 100.0) # Proteção principal
                        operacoes_abertas[mem_key] = {
                            "id": op['id'], "entrada": preco_ent, "lucro_maximo": 0.0, 
                            "trailing_ativo": False, "capital_alocado": cap_alocado_seguro, "tipo_ordem": tipo,
                            "hora_criacao": time.time() 
                        }
        print("✅ Memória restaurada com sucesso!")

# ==========================================
# 3. O CÉREBRO: IA + RADAR MULTIPAR
# ==========================================
def reuniao_com_ia_gestora(usuario, symbol, preco_atual, rsi_atual, volatilidade, marcha, raio_x_book, tendencia_macro, taxa_funding, indice_medo, radar_baleias):
    global timestamps_ia, teses_ia
    id_banco = usuario.get("id") 
    mem_key = f"{usuario.get('usuario_id')}_{symbol}"
    
    rsi_antigo = usuario.get("rsi_alvo", 40)
    direcao_antiga = usuario.get("direcao_operacao", "Compra")
    hist = historico_hora.get(mem_key, {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0})
    
    prompt = f"""
    Ativo: {symbol}. Preço: {preco_atual:.2f}. RSI: {rsi_atual:.2f}.
    Dados: Vol {volatilidade:.2f}% ({marcha}), Tendência {tendencia_macro}, {raio_x_book}, Baleias: {radar_baleias}.
    Placar: Reais {hist['reais_vitorias']}/{hist['reais_derrotas']}, Fantasmas {hist['fantasma_vitorias']}/{hist['fantasma_derrotas']}.
    Tarefa: Defina 'direcao_operacao' e 'rsi_alvo'. Use Fantasmas para validar Baleias.
    Responda APENAS JSON:
    {{
      "direcao_operacao": "Compra",
      "rsi_alvo": 35, 
      "gatilho_trailing": 0.4, 
      "distancia_trailing": 0.2, 
      "observacao_ia": "Sua tese curta e direta..."
    }}
    """
    try:
        res = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        nova_regra = json.loads(res.text.replace("```json", "").replace("```", "").strip())
        
        # 1. Guarda a hora exata
        hora_reuniao = obter_data_hora_br().strftime('%H:%M:%S')
        timestamps_ia[symbol] = hora_reuniao
        
        # 2. Guarda a tese desta moeda específica
        teses_ia[symbol] = nova_regra.get("observacao_ia", "Tese atualizada.")
        
        # 3. Compila o Super Relatório Triplo
        relatorio_master = f"🔹 [BTC/USDT] {timestamps_ia['BTC/USDT']}:\n{teses_ia['BTC/USDT']}\n\n"
        relatorio_master += f"🔹 [ETH/USDT] {timestamps_ia['ETH/USDT']}:\n{teses_ia['ETH/USDT']}\n\n"
        relatorio_master += f"🔹 [SOL/USDT] {timestamps_ia['SOL/USDT']}:\n{teses_ia['SOL/USDT']}"
        
        # Envia o relatório fundido para a Base44
        nova_regra["observacao_ia"] = relatorio_master
        nova_regra["rsi_alvo_compra"] = nova_regra["rsi_alvo"] 
        api_base44("PUT", ENDPOINTS["controle"], nova_regra, id_registro=id_banco)
        
        historico_hora[mem_key] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
        print(f"✅ [NOVA DIRETRIZ {symbol}] Direção: {nova_regra['direcao_operacao']} | Alvo: {nova_regra['rsi_alvo']}")
    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            print(f"⏳ [IA {symbol}] API Gratuita em tempo de espera. Mantendo a estratégia atual.")
        else:
            print(f"❌ Erro na IA ({symbol}): {e}")

# ==========================================
# 4. O OPERÁRIO: LOOP TURBO COM TIME-DECAY E FDS
# ==========================================
def iniciar_loop():
    print("🚀 MIRAQUANTIA SCALPER - SUPER RELATÓRIO TRIPLO E BLINDAGEM DE ERROS ATIVOS")
    global ultima_reuniao_ia, ordens_fantasma, historico_hora, operacoes_abertas, data_operacao_usuario, timestamps_ia, teses_ia
    
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
            todas_ops_db = api_base44("GET", ENDPOINTS["operacao"])
            
            if not configs: 
                time.sleep(10); continue

            ops_fechadas_db = [op['id'] for op in todas_ops_db if op.get("status") == "Fechada"] if todas_ops_db else []
            ex = ccxt.bybit()
            dados_mercado = {}
            radar_baleias_dados = {}
            
            agora_br = obter_data_hora_br()
            hoje_data = agora_br.strftime('%Y-%m-%d')
            hora_atual = agora_br.strftime('%H:%M:%S')
            fds_ativo = is_fim_de_semana()
            
            if fds_ativo:
                print(f"\n[{hora_atual}] 🔭 ESCANEANDO O MERCADO... [MODO FIM DE SEMANA ATIVO]")
            else:
                print(f"\n[{hora_atual}] 🔭 ESCANEANDO O MERCADO...")
            
            for symbol in MOEDAS_ATIVAS:
                p, r, v, rb, tm, tf = ler_mercado(ex, symbol)
                if p is not None:
                    dados_mercado[symbol] = (p, r, v, rb, tm, tf)
                    radar_baleias_dados[symbol] = obter_radar_baleias(symbol)

            for user in configs:
                uid = user.get("usuario_id")
                modo = str(user.get("modo_operacao", "Demo")).capitalize()
                meta = float(user.get("meta_diaria_porcentagem") or 2.0)
                limite_perda = float(user.get("risco_maximo_porcentagem") or 10.0)

                if not user.get("status_bot"): continue
                
                if uid not in data_operacao_usuario: 
                    print(f"🔄 [{hora_atual}] Sincronizando meta diária com a Base44 para hoje ({hoje_data})...")
                    lucro_hoje = reconciliar_lucro_diario(uid, user['id'], hoje_data, todas_ops_db)
                    data_operacao_usuario[uid] = hoje_data
                elif data_operacao_usuario[uid] != hoje_data:
                    print(f"🕛 MEIA NOITE NO BRASIL! Iniciando novo dia de operações para {uid}...")
                    lucro_hoje = reconciliar_lucro_diario(uid, user['id'], hoje_data, todas_ops_db)
                    data_operacao_usuario[uid] = hoje_data
                else:
                    lucro_hoje = float(user.get("lucro_hoje_porcentagem") or 0.0)

                if lucro_hoje >= meta: 
                    print(f"🏆 Meta diária atingida ({lucro_hoje:.2f}%). Robô em stand-by até meia-noite.")
                    continue
                if lucro_hoje <= -limite_perda: 
                    print(f"🛑 Limite de perda diário atingido ({lucro_hoje:.2f}%). Proteção ativada.")
                    continue

                reg_saldo = next((s for s in saldos if s['usuario_id'] == uid), None) if saldos else None
                saldo_total = float(reg_saldo.get('saldo_demo') or 100.0)
                capital_preso = sum(op["capital_alocado"] for k, op in operacoes_abertas.items() if k.startswith(f"{uid}_"))
                saldo_livre = saldo_total - capital_preso
                
                capital_op = float(user.get("capital_por_operacao") or (saldo_livre * 0.30))

                for symbol in MOEDAS_ATIVAS:
                    if symbol not in dados_mercado: continue
                    preco, rsi, volatilidade, raio_x_book, tendencia_macro, taxa_funding = dados_mercado[symbol]
                    mem_key = f"{uid}_{symbol}"
                    
                    if mem_key not in ultima_reuniao_ia: 
                        offset_moeda = MOEDAS_ATIVAS.index(symbol) * 60
                        ultima_reuniao_ia[mem_key] = time.time() - 1800 + offset_moeda
                        
                    if mem_key not in ordens_fantasma: ordens_fantasma[mem_key] = []
                    if mem_key not in historico_hora: historico_hora[mem_key] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}

                    # GESTÃO LIVE & TIME-DECAY
                    if mem_key in operacoes_abertas:
                        op = operacoes_abertas[mem_key]
                        
                        if op["id"] in ops_fechadas_db:
                            print(f"   🧑‍💻 [{symbol}] INTERVENÇÃO HUMANA DETETADA! Fechada pelo Dashboard.")
                            ordem_db = next((x for x in todas_ops_db if x['id'] == op['id']), None)
                            if ordem_db:
                                lucro_pct_final = float(ordem_db.get("lucro_porcentagem") or 0.0)
                                lucro_fin_final = float(ordem_db.get("lucro_financeiro") or 0.0)
                                atualizar_dashboard_total(user, lucro_pct_final, lucro_fin_final)
                                if lucro_pct_final > 0: historico_hora[mem_key]['reais_vitorias'] += 1
                                else: historico_hora[mem_key]['reais_derrotas'] += 1
                                
                            del operacoes_abertas[mem_key]
                            continue
                        
                        lucro_pct = ((preco - op["entrada"]) / op["entrada"]) * 100 if op["tipo_ordem"] == "Compra" else ((op["entrada"] - preco) / op["entrada"]) * 100
                        if lucro_pct > op["lucro_maximo"]: op["lucro_maximo"] = lucro_pct
                        lucro_fin = op["capital_alocado"] * (lucro_pct / 100)

                        api_base44("PUT", ENDPOINTS["operacao"], {"preco_saida": preco, "lucro_porcentagem": lucro_pct, "lucro_financeiro": lucro_fin}, id_registro=op["id"])

                        tempo_aberta = time.time() - op.get("hora_criacao", time.time())
                        if volatilidade < 0.5: tempo_limite_fuga = 900 
                        elif volatilidade <= 1.5: tempo_limite_fuga = 600
                        else: tempo_limite_fuga = 420 

                        modo_fuga = False
                        if tempo_aberta > tempo_limite_fuga:
                            modo_fuga = True

                        if fds_ativo:
                            gatilho_ts = 0.15
                            distancia_ts = 0.05
                        else:
                            gatilho_ts = float(user.get("gatilho_trailing", 0.4))
                            distancia_ts = float(user.get("distancia_trailing", 0.2))

                        vender = False
                        motivo_venda = ""

                        if modo_fuga and lucro_pct >= 0.05:
                            vender = True
                            motivo_venda = "Fuga por Tempo Esgotado"
                        elif lucro_pct >= gatilho_ts and not op["trailing_ativo"]: 
                            op["trailing_ativo"] = True
                            print(f"   🛡️ [{symbol}] TRAILING ATIVADO! Garantindo o lucro...")
                        
                        if not vender and op["trailing_ativo"]:
                            if lucro_pct <= (op["lucro_maximo"] - distancia_ts): 
                                vender = True
                                motivo_venda = "Trailing Executado"
                        elif not vender and lucro_pct <= -0.35: 
                            vender = True
                            motivo_venda = "Stop Loss de Proteção"

                        if vender:
                            print(f"   🤖 [{symbol}] FECHADO PELO ROBÔ: {motivo_venda} | Lucro: {lucro_pct:.2f}%")
                            api_base44("PUT", ENDPOINTS["operacao"], {
                                "preco_saida": preco, 
                                "lucro_porcentagem": lucro_pct, 
                                "lucro_financeiro": lucro_fin, 
                                "status": "Fechada",
                                "motivo_fechamento": motivo_venda
                            }, id_registro=op["id"])
                            atualizar_dashboard_total(user, lucro_pct, lucro_fin)
                            del operacoes_abertas[mem_key]

                    else:
                        direcao = user.get("direcao_operacao", "Compra")
                        limite_rsi = float(user.get("rsi_alvo", 40))
                        if (direcao == "Compra" and rsi <= limite_rsi) or (direcao == "Venda" and rsi >= limite_rsi):
                            data_hora_br = obter_data_hora_br().isoformat()
                            res = api_base44("POST", ENDPOINTS["operacao"], {"usuario_id": uid, "par_moeda": symbol, "tipo_ordem": direcao, "categoria_ordem": modo, "preco_entrada": preco, "data_hora": data_hora_br, "status": "Aberta"})
                            if res and 'id' in res: operacoes_abertas[mem_key] = {"id": res['id'], "entrada": preco, "lucro_maximo": 0.0, "trailing_ativo": False, "capital_alocado": capital_op, "tipo_ordem": direcao, "hora_criacao": time.time()}

                    # ORDEM FANTASMA
                    for f in ordens_fantasma[mem_key][:]:
                        bateu_alvo = (f["tipo_ordem"] == "Compra" and preco >= f["alvo"]) or (f["tipo_ordem"] == "Venda" and preco <= f["alvo"])
                        bateu_stop = (f["tipo_ordem"] == "Compra" and preco <= f["stop"]) or (f["tipo_ordem"] == "Venda" and preco >= f["stop"])
                        timeout = (time.time() - f.get("hora_criacao", time.time())) > 2700

                        if bateu_alvo or bateu_stop or timeout:
                            lucro_pct = ((preco - f['entrada']) / f['entrada']) * 100 if f["tipo_ordem"] == "Compra" else ((f['entrada'] - preco) / f['entrada']) * 100
                                
                            if lucro_pct > 0: historico_hora[mem_key]['fantasma_vitorias'] += 1
                            else: historico_hora[mem_key]['fantasma_derrotas'] += 1
                            
                            lucro_financeiro = f.get("capital_alocado", 100) * (lucro_pct / 100)
                            motivo_fantasma = "Alvo Fantasma Atingido" if bateu_alvo else "Stop Fantasma" if bateu_stop else "Timeout Fantasma"
                            
                            api_base44("PUT", ENDPOINTS["operacao"], {
                                "preco_saida": preco, "lucro_porcentagem": abs(lucro_pct),
                                "lucro_financeiro": lucro_financeiro, "status": "Fechada",
                                "motivo_fechamento": motivo_fantasma
                            }, id_registro=f['id'])
                            ordens_fantasma[mem_key].remove(f)

                    freq_ia = 900 if volatilidade > 1.5 else 1800
                    if time.time() - ultima_reuniao_ia[mem_key] > freq_ia:
                        reuniao_com_ia_gestora(user, symbol, preco, rsi, volatilidade, "TURBO", raio_x_book, tendencia_macro, taxa_funding, indice_medo, radar_baleias_dados[symbol])
                        ultima_reuniao_ia[mem_key] = time.time()
                        time.sleep(3)

            time.sleep(10)
            
        except Exception as e:
            print(f"⚠️ ERRO CRÍTICO NO LOOP: {e}")
            traceback.print_exc()
            time.sleep(10)

if __name__ == "__main__":
    iniciar_loop()
