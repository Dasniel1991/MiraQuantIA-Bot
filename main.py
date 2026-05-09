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

SYMBOL = 'BTC/USDT'

# Memória do Robô
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

def ler_mercado(exchange):
    try:
        velas = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=15)
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
            order_book = exchange.fetch_order_book(SYMBOL, limit=100)
            compras_m = sum(bid[0] * bid[1] for bid in order_book['bids']) / 1_000_000
            vendas_m = sum(ask[0] * ask[1] for ask in order_book['asks']) / 1_000_000
            raio_x_book = f"${compras_m:.2f}M Compras vs ${vendas_m:.2f}M Vendas"
        except: raio_x_book = "Indisponível"

        try:
            velas_1h = exchange.fetch_ohlcv(SYMBOL, timeframe='1h', limit=200)
            fechamentos_1h = [v[4] for v in velas_1h]
            media_200 = sum(fechamentos_1h) / len(fechamentos_1h)
            tendencia_macro = "ALTA" if preco_atual > media_200 else "BAIXA"
        except: tendencia_macro = "Indisponível"

        try:
            funding = exchange.fetch_funding_rate(SYMBOL)
            taxa_funding = funding['fundingRate'] * 100 
        except: taxa_funding = 0.0
            
        return preco_atual, rsi, volatilidade, raio_x_book, tendencia_macro, taxa_funding
    except Exception as e:
        print(f"Erro na leitura do mercado: {e}")
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

# ==========================================
# 3. O CÉREBRO: IA INSTITUCIONAL (LONG/SHORT)
# ==========================================
def reuniao_com_ia_gestora(usuario, preco_atual, rsi_atual, volatilidade, marcha, raio_x_book, tendencia_macro, taxa_funding, indice_medo):
    uid = usuario.get("usuario_id")
    id_banco = usuario.get("id") 
    rsi_antigo = usuario.get("rsi_alvo", 40)
    direcao_antiga = usuario.get("direcao_operacao", "Compra")
    hist = historico_hora[uid]
    
    print(f"\n🧠 [REUNIÃO DE DIRETORIA] IA Analisando Dados para {uid}...")
    prompt = f"""
    Ativo: {SYMBOL}. Preço: {preco_atual:.2f}. RSI: {rsi_atual:.2f}. Direção Anterior: {direcao_antiga} | RSI Alvo Anterior: {rsi_antigo}.
    
    DADOS INSTITUCIONAIS:
    1. Volatilidade: {volatilidade:.2f}% (Marcha {marcha})
    2. EMA 200: Tendência de {tendencia_macro}.
    3. Order Book: {raio_x_book}.
    4. Funding Rate: {taxa_funding:.4f}%.
    5. Sentimento Global: {indice_medo}.
    
    PLACAR DO ROBÔ:
    Vitórias: {hist['reais_vitorias']} | Derrotas: {hist['reais_derrotas']}
    
    TAREFA COMO DIRETOR DE RISCO SNIPER (SCALPING DE IMPULSO):
    - Escolha a 'direcao_operacao': "Compra" (Long) ou "Venda" (Short). Siga a tendência macro (EMA 200).
    - Defina o 'rsi_alvo'. Se for Compra, o robô atira quando o RSI cair para este alvo (ex: 30). Se for Venda, o robô atira quando o RSI subir para este alvo (ex: 70).
    - Mantenha alvos curtos e proteções justas para um Scalping eficiente.
    
    Responda APENAS um JSON válido no formato:
    {{
      "direcao_operacao": "Compra",
      "rsi_alvo": 35, 
      "gatilho_trailing": 0.4, 
      "distancia_trailing": 0.2, 
      "status_mercado": "🟢 Tendência de Alta Confirmada",
      "observacao_ia": "Sua tese..."
    }}
    """
    try:
        res = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        nova_regra = json.loads(res.text.replace("```json", "").replace("```", "").strip())
        
        # Garante retrocompatibilidade se a base de dados ainda tiver a nomenclatura antiga
        nova_regra["rsi_alvo_compra"] = nova_regra["rsi_alvo"] 
        
        api_base44("PUT", ENDPOINTS["controle"], nova_regra, id_registro=id_banco)
        print(f"✅ [NOVA DIRETRIZ] Direção: {nova_regra['direcao_operacao']} | RSI Alvo: {nova_regra['rsi_alvo']} | Status: {nova_regra['status_mercado']}")
        historico_hora[uid] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
    except Exception as e: print(f"❌ [ERRO IA]: {e}")

# ==========================================
# 4. O OPERÁRIO: LOOP PRINCIPAL
# ==========================================
def iniciar_loop():
    print("🚀 MIRAQUANTIA SCALPER - LONG/SHORT E STOP CURTO ATIVADOS")
    global ultima_reuniao_ia, ordens_fantasma, historico_hora, operacoes_abertas, data_operacao_usuario
    
    indice_medo = obter_medo_e_ganancia()
    ultimo_update_medo = time.time()
    
    while True:
        try:
            if time.time() - ultimo_update_medo > 21600:
                indice_medo = obter_medo_e_ganancia()
                ultimo_update_medo = time.time()

            configs = api_base44("GET", ENDPOINTS["controle"])
            saldos = api_base44("GET", ENDPOINTS["saldo"]) 
            
            if not configs: time.sleep(60); continue

            ex = ccxt.bybit()
            preco, rsi, volatilidade, raio_x_book, tendencia_macro, taxa_funding = ler_mercado(ex)
            
            if preco is None:
                time.sleep(60); continue

            if volatilidade < 0.5:
                tempo_espera_ia = 3600; marcha = "LENTA"
            elif volatilidade <= 1.5:
                tempo_espera_ia = 1800; marcha = "NORMAL"
            else:
                tempo_espera_ia = 900; marcha = "TURBO"

            hora_atual = datetime.now().strftime('%H:%M:%S')
            hoje_data = datetime.now().strftime('%Y-%m-%d')
            
            print(f"\n[{hora_atual}] 📊 MERCADO: Preço ${preco} | RSI: {rsi:.2f}")

            for user in configs:
                uid = user.get("usuario_id")
                modo = str(user.get("modo_operacao", "Demo")).capitalize()
                meta = float(user.get("meta_diaria_porcentagem") or 2.0)
                limite_perda = float(user.get("risco_maximo_porcentagem") or 10.0)
                lucro_hoje = float(user.get("lucro_hoje_porcentagem") or 0.0)

                if not user.get("status_bot"): continue
                
                reg_saldo = next((s for s in saldos if s['usuario_id'] == uid), None) if saldos else None
                saldo_demo_atual = float(reg_saldo.get('saldo_demo', 100)) if reg_saldo else 100.0
                capital_operacao = float(user.get("capital_por_operacao") or saldo_demo_atual)

                if uid not in ultima_reuniao_ia: ultima_reuniao_ia[uid] = 0
                if uid not in ordens_fantasma: ordens_fantasma[uid] = []
                if uid not in historico_hora: historico_hora[uid] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
                if uid not in data_operacao_usuario: data_operacao_usuario[uid] = hoje_data

                # RESET DA MEIA NOITE
                if data_operacao_usuario[uid] != hoje_data:
                    api_base44("PUT", ENDPOINTS["controle"], {"lucro_hoje_porcentagem": 0.0, "lucro_hoje": 0.0}, id_registro=user['id'])
                    data_operacao_usuario[uid] = hoje_data
                    lucro_hoje = 0.0

                # TRAVAS DIÁRIAS
                if lucro_hoje >= meta: continue
                if lucro_hoje <= -limite_perda: continue

                # NOVOS PARÂMETROS DE DIREÇÃO E ALVO
                direcao = user.get("direcao_operacao", "Compra")
                limite_rsi = float(user.get("rsi_alvo", user.get("rsi_alvo_compra", 40)))
                gatilho_ts = float(user.get("gatilho_trailing", 0.4))
                distancia_ts = float(user.get("distancia_trailing", 0.2))
                stop_loss_rigido = -0.35 # CORTE NA RAIZ!

                # REUNIÃO DA IA
                if time.time() - ultima_reuniao_ia[uid] > tempo_espera_ia:
                    reuniao_com_ia_gestora(user, preco, rsi, volatilidade, marcha, raio_x_book, tendencia_macro, taxa_funding, indice_medo)
                    ultima_reuniao_ia[uid] = time.time()

                # GESTÃO DE ORDENS ABERTAS (LUCRO BILATERAL)
                if uid in operacoes_abertas:
                    op = operacoes_abertas[uid]
                    
                    # CÁLCULO DE LUCRO DEPENDE DA DIREÇÃO!
                    if op["tipo_ordem"] == "Compra":
                        lucro_pct = ((preco - op["entrada"]) / op["entrada"]) * 100
                    else: # Venda (Ganha quando o preço cai)
                        lucro_pct = ((op["entrada"] - preco) / op["entrada"]) * 100
                    
                    if lucro_pct > op["lucro_maximo"]: op["lucro_maximo"] = lucro_pct

                    vender = False; motivo_venda = ""

                    if lucro_pct >= gatilho_ts and not op["trailing_ativo"]:
                        op["trailing_ativo"] = True
                        print(f"   🛡️ [{uid}] TRAILING ATIVADO! Garantindo o lucro do scalper...")

                    if op["trailing_ativo"]:
                        linha_de_venda = op["lucro_maximo"] - distancia_ts
                        print(f"   🏄‍♂️ [{uid}] ({op['tipo_ordem']}) Atual: {lucro_pct:.2f}% | Topo: {op['lucro_maximo']:.2f}% | Stop Seguro: {linha_de_venda:.2f}%")
                        if lucro_pct <= linha_de_venda:
                            vender = True; motivo_venda = "Trailing Executado"
                    else:
                        print(f"   👁️ [{uid}] ({op['tipo_ordem']}) Vigiando... Lucro: {lucro_pct:.2f}%")
                        if lucro_pct <= stop_loss_rigido:
                            vender = True; motivo_venda = "Stop Loss de Proteção (Corte Rápido)"

                    if vender:
                        lucro_financeiro = op["capital_alocado"] * (lucro_pct / 100)
                        print(f"   💰 [{uid}] FECHANDO ORDEM ({motivo_venda})! Lucro: {lucro_pct:.2f}% (${lucro_financeiro:.2f})")
                        
                        api_base44("PUT", ENDPOINTS["operacao"], {
                            "preco_saida": preco, "lucro_porcentagem": lucro_pct, 
                            "lucro_financeiro": lucro_financeiro, "status": "Fechada"
                        }, id_registro=op["id"])
                        
                        atualizar_dashboard_total(user, lucro_pct, lucro_financeiro)
                        
                        if lucro_pct > 0: historico_hora[uid]['reais_vitorias'] += 1
                        else: historico_hora[uid]['reais_derrotas'] += 1
                        del operacoes_abertas[uid]

                # PROCURA NOVAS ENTRADAS (SNIPER SCALPING)
                else:
                    sinal_compra = (direcao == "Compra" and rsi <= limite_rsi)
                    sinal_venda = (direcao == "Venda" and rsi >= limite_rsi)

                    if sinal_compra or sinal_venda:
                        print(f"   🎯 [{uid}] SINAL DE {direcao.upper()}! RSI ({rsi:.2f}) atingiu o alvo ({limite_rsi}). Alocando ${capital_operacao:.2f}")
                        res = api_base44("POST", ENDPOINTS["operacao"], {
                            "usuario_id": uid, "par_moeda": SYMBOL, "tipo_ordem": direcao, # Agora pode ser Venda!
                            "categoria_ordem": modo, "preco_entrada": preco, "data_hora": datetime.now().isoformat(), "status": "Aberta"
                        })
                        if res and 'id' in res: 
                            operacoes_abertas[uid] = {
                                "id": res['id'], "entrada": preco, "lucro_maximo": 0.0, 
                                "trailing_ativo": False, "capital_alocado": capital_operacao,
                                "tipo_ordem": direcao
                            }

                    # FANTASMAS (Também adaptados para Long/Short)
                    elif (direcao == "Compra" and rsi <= (limite_rsi + 15)) or (direcao == "Venda" and rsi >= (limite_rsi - 15)):
                        res = api_base44("POST", ENDPOINTS["operacao"], {
                            "usuario_id": uid, "par_moeda": SYMBOL, "tipo_ordem": direcao,
                            "categoria_ordem": "Fantasma", "preco_entrada": preco, "data_hora": datetime.now().isoformat(), "status": "Aberta"
                        })
                        if res and 'id' in res:
                            alvo = preco * (1 + (gatilho_ts/100)) if direcao == "Compra" else preco * (1 - (gatilho_ts/100))
                            stop = preco * (1 + (stop_loss_rigido/100)) if direcao == "Compra" else preco * (1 - (stop_loss_rigido/100))
                            ordens_fantasma[uid].append({
                                "id": res['id'], "entrada": preco, "alvo": alvo, "stop": stop, "tipo_ordem": direcao
                            })

                # ACOMPANHA FANTASMAS
                for f in ordens_fantasma[uid][:]:
                    bateu_alvo = (f["tipo_ordem"] == "Compra" and preco >= f["alvo"]) or (f["tipo_ordem"] == "Venda" and preco <= f["alvo"])
                    bateu_stop = (f["tipo_ordem"] == "Compra" and preco <= f["stop"]) or (f["tipo_ordem"] == "Venda" and preco >= f["stop"])

                    if bateu_alvo:
                        historico_hora[uid]['fantasma_vitorias'] += 1
                        lucro_pct = ((preco - f['entrada']) / f['entrada']) * 100 if f["tipo_ordem"] == "Compra" else ((f['entrada'] - preco) / f['entrada']) * 100
                        api_base44("PUT", ENDPOINTS["operacao"], {"preco_saida": preco, "lucro_porcentagem": abs(lucro_pct), "status": "Fechada"}, id_registro=f['id'])
                        ordens_fantasma[uid].remove(f)
                    
                    elif bateu_stop:
                        historico_hora[uid]['fantasma_derrotas'] += 1
                        lucro_pct = ((preco - f['entrada']) / f['entrada']) * 100 if f["tipo_ordem"] == "Compra" else ((f['entrada'] - preco) / f['entrada']) * 100
                        api_base44("PUT", ENDPOINTS["operacao"], {"preco_saida": preco, "lucro_porcentagem": -abs(lucro_pct), "status": "Fechada"}, id_registro=f['id'])
                        ordens_fantasma[uid].remove(f)

            time.sleep(60)
        except Exception as e: print(f"Erro Crítico: {e}"); time.sleep(60)

if __name__ == "__main__":
    iniciar_loop()
    # ==========================================
# 4. O OPERÁRIO: LOOP PRINCIPAL
# ==========================================
def iniciar_loop():
    print("🚀 MIRAQUANTIA SCALPER - LONG/SHORT E STOP CURTO ATIVADOS")
    global ultima_reuniao_ia, ordens_fantasma, historico_hora, operacoes_abertas, data_operacao_usuario
    
    indice_medo = obter_medo_e_ganancia()
    ultimo_update_medo = time.time()
    
    teste_forcar_venda = False  # <--- TRAVA DO TESTE CRIADA AQUI
    
    while True:
        try:
            if time.time() - ultimo_update_medo > 21600:
                indice_medo = obter_medo_e_ganancia()
                ultimo_update_medo = time.time()

            configs = api_base44("GET", ENDPOINTS["controle"])
            saldos = api_base44("GET", ENDPOINTS["saldo"]) 
            
            if not configs: time.sleep(60); continue

            ex = ccxt.bybit()
            preco, rsi, volatilidade, raio_x_book, tendencia_macro, taxa_funding = ler_mercado(ex)
            
            if preco is None:
                time.sleep(60); continue

            # --- INÍCIO DO BLOCO DE TESTE ---
            if not teste_forcar_venda:
                print("🧪 [TESTE] Forçando Ordem Fantasma de VENDA para conferência visual...")
                test_user_id = configs[0].get("usuario_id")
                res_teste = api_base44("POST", ENDPOINTS["operacao"], {
                    "usuario_id": test_user_id, "par_moeda": SYMBOL, "tipo_ordem": "Venda",
                    "categoria_ordem": "Fantasma", "preco_entrada": preco, 
                    "data_hora": datetime.now().isoformat(), "status": "Aberta"
                })
                if res_teste and 'id' in res_teste:
                    ordens_fantasma[test_user_id].append({
                        "id": res_teste['id'], "entrada": preco, 
                        "alvo": preco * 0.996, "stop": preco * 1.0035, "tipo_ordem": "Venda"
                    })
                print("🧪 [TESTE] Ordem de Venda enviada! Verifique o Dashboard.")
                teste_forcar_venda = True # Trava ativada para não enviar de novo
            # --- FIM DO BLOCO DE TESTE ---

            # ... (aqui continua o resto do seu código normalmente) ...
