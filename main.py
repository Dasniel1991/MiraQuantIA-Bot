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
    """Busca o Índice Global de Sentimento do Mercado"""
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        dados = res.json()
        valor = int(dados['data'][0]['value'])
        classificacao = dados['data'][0]['value_classification']
        return f"{valor} ({classificacao})"
    except:
        return "Neutro"

def ler_mercado(exchange):
    """Lê todas as métricas institucionais do ativo"""
    try:
        # 1. Micro-Tendência (15m) e Volatilidade
        velas = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=15)
        fechamentos = [v[4] for v in velas]
        maximas = [v[2] for v in velas]
        minimas = [v[3] for v in velas]
        preco_atual = fechamentos[-1]
        
        volatilidade = ((max(maximas) - min(minimas)) / min(minimas)) * 100

        # 2. RSI Curto
        ganhos, perdas = [], []
        for i in range(1, len(fechamentos)):
            diff = fechamentos[i] - fechamentos[i-1]
            if diff > 0: ganhos.append(diff)
            else: perdas.append(abs(diff))
        media_ganhos = sum(ganhos) / 14 if ganhos else 0
        media_perdas = sum(perdas) / 14 if perdas else 0
        rsi = 100 if media_perdas == 0 else 100 - (100 / (1 + (media_ganhos / media_perdas)))
        
        # 3. Raio-X do Livro de Ofertas
        try:
            order_book = exchange.fetch_order_book(SYMBOL, limit=100)
            compras_m = sum(bid[0] * bid[1] for bid in order_book['bids']) / 1_000_000
            vendas_m = sum(ask[0] * ask[1] for ask in order_book['asks']) / 1_000_000
            raio_x_book = f"${compras_m:.2f}M Compras vs ${vendas_m:.2f}M Vendas"
        except: raio_x_book = "Indisponível"

        # 4. Macro-Tendência (Média 200 Horas)
        try:
            velas_1h = exchange.fetch_ohlcv(SYMBOL, timeframe='1h', limit=200)
            fechamentos_1h = [v[4] for v in velas_1h]
            media_200 = sum(fechamentos_1h) / len(fechamentos_1h)
            tendencia_macro = "ALTA" if preco_atual > media_200 else "BAIXA"
        except: tendencia_macro = "Indisponível"

        # 5. Funding Rate (Alavancagem do Varejo)
        try:
            funding = exchange.fetch_funding_rate(SYMBOL)
            taxa_funding = funding['fundingRate'] * 100 # Em %
        except: taxa_funding = 0.0
            
        return preco_atual, rsi, volatilidade, raio_x_book, tendencia_macro, taxa_funding
    except Exception as e:
        print(f"Erro na leitura do mercado: {e}")
        return None, None, None, None, None, None

def atualizar_dashboard_total(usuario, lucro_operacao_pct):
    uid = usuario['usuario_id']
    saldos = api_base44("GET", ENDPOINTS["saldo"])
    if saldos:
        reg_saldo = next((s for s in saldos if s['usuario_id'] == uid), None)
        if reg_saldo:
            valor_financeiro = 100 * (lucro_operacao_pct / 100)
            novo_saldo = float(reg_saldo.get('saldo_demo', 0)) + valor_financeiro
            api_base44("PUT", ENDPOINTS["saldo"], {"saldo_demo": novo_saldo}, id_registro=reg_saldo['id'])

    lucro_acumulado = float(usuario.get("lucro_hoje_porcentagem") or 0.0) + lucro_operacao_pct
    api_base44("PUT", ENDPOINTS["controle"], {
        "lucro_hoje_porcentagem": lucro_acumulado,
        "lucro_hoje": 100 * (lucro_acumulado / 100)
    }, id_registro=usuario['id'])

# ==========================================
# 3. O CÉREBRO: IA INSTITUCIONAL
# ==========================================
def reuniao_com_ia_gestora(usuario, preco_atual, rsi_atual, volatilidade, marcha, raio_x_book, tendencia_macro, taxa_funding, indice_medo):
    uid = usuario.get("usuario_id")
    id_banco = usuario.get("id") 
    rsi_antigo = usuario.get("rsi_alvo_compra", 40)
    hist = historico_hora[uid]
    
    print(f"\n🧠 [REUNIÃO DE DIRETORIA] IA Analisando Dados Institucionais para {uid}...")
    prompt = f"""
    Ativo: {SYMBOL}. Preço: {preco_atual:.2f}. RSI: {rsi_atual:.2f}. Alvo de Compra Antigo: {rsi_antigo}.
    
    DADOS INSTITUCIONAIS GLOBAIS:
    1. Volatilidade e Marcha: {volatilidade:.2f}% (Marcha {marcha})
    2. Bússola Macro (EMA 200): O mercado está em tendência de {tendencia_macro}.
    3. Order Book (Baleias): {raio_x_book}.
    4. Alavancagem Varejo (Funding Rate): {taxa_funding:.4f}% (Positivo = Varejo apostando na alta; Negativo = Apostando na baixa).
    5. Sentimento Global (Fear & Greed): {indice_medo}.
    
    PLACAR DO ROBÔ (Último ciclo):
    Reais - Vitórias: {hist['reais_vitorias']} | Derrotas: {hist['reais_derrotas']}
    Fantasmas - Vitórias: {hist['fantasma_vitorias']} | Derrotas: {hist['fantasma_derrotas']}
    
    TAREFA: Seja o Diretor de Risco do fundo de investimentos.
    - Se a tendência macro for BAIXA e o Funding for POSITIVO, é perigoso comprar (Risco de liquidação). Diminua drasticamente o 'rsi_alvo_compra'.
    - Se houver pânico (Medo) e uma enorme parede de compras no Order Book, aumente o 'rsi_alvo_compra' e prepare-se para surfar a alta ajustando o Trailing Stop ('gatilho_trailing' e 'distancia_trailing').
    
    Responda APENAS um JSON válido:
    {{"rsi_alvo_compra": 40, "gatilho_trailing": 0.6, "distancia_trailing": 0.3, "observacao_ia": "Sua tese de investimento..."}}
    """
    try:
        res = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        nova_regra = json.loads(res.text.replace("```json", "").replace("```", "").strip())
        
        api_base44("PUT", ENDPOINTS["controle"], nova_regra, id_registro=id_banco)
        print(f"✅ [NOVA DIRETRIZ] RSI: {nova_regra['rsi_alvo_compra']} | Trailing: {nova_regra['gatilho_trailing']}% (-{nova_regra['distancia_trailing']}%)")
        print(f"📝 Tese da IA: {nova_regra['observacao_ia']}\n")
        historico_hora[uid] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
    except Exception as e: print(f"❌ [ERRO IA]: {e}")

# ==========================================
# 4. O OPERÁRIO: LOOP PRINCIPAL
# ==========================================
def iniciar_loop():
    print("🚀 MIRAQUANTIA PRO MAX - ALGORITMO INSTITUCIONAL ATIVADO")
    global ultima_reuniao_ia, ordens_fantasma, historico_hora, operacoes_abertas, data_operacao_usuario
    
    # Busca o sentimento global fora do loop para não sobrecarregar a API
    indice_medo = obter_medo_e_ganancia()
    ultimo_update_medo = time.time()
    
    while True:
        try:
            # Atualiza o índice de medo global a cada 6 horas
            if time.time() - ultimo_update_medo > 21600:
                indice_medo = obter_medo_e_ganancia()
                ultimo_update_medo = time.time()

            configs = api_base44("GET", ENDPOINTS["controle"])
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
            
            print(f"\n[{hora_atual}] 📊 MERCADO: Preço ${preco} | RSI: {rsi:.2f} | Volatilidade: {volatilidade:.2f}%")
            print(f"   🏛️ INSTITUCIONAL: Macro em {tendencia_macro} | Sentimento: {indice_medo} | Book: {raio_x_book} | Funding: {taxa_funding:.4f}%")

            for user in configs:
                uid = user.get("usuario_id")
                modo = str(user.get("modo_operacao", "Demo")).capitalize()
                meta = float(user.get("meta_diaria_porcentagem") or 2.0)
                limite_perda = float(user.get("risco_maximo_porcentagem") or 10.0)
                lucro_hoje = float(user.get("lucro_hoje_porcentagem") or 0.0)

                if not user.get("status_bot"): continue
                
                if uid not in ultima_reuniao_ia: ultima_reuniao_ia[uid] = 0
                if uid not in ordens_fantasma: ordens_fantasma[uid] = []
                if uid not in historico_hora: historico_hora[uid] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
                if uid not in data_operacao_usuario: data_operacao_usuario[uid] = hoje_data

                if data_operacao_usuario[uid] != hoje_data:
                    print(f"🌅 Novo dia detectado para {uid}! Zerando metas no Dashboard...")
                    api_base44("PUT", ENDPOINTS["controle"], {"lucro_hoje_porcentagem": 0.0, "lucro_hoje": 0.0}, id_registro=user['id'])
                    data_operacao_usuario[uid] = hoje_data
                    lucro_hoje = 0.0

                if lucro_hoje >= meta:
                    print(f"   🎉 [{uid}] META BATIDA ({lucro_hoje:.2f}% de {meta}%). Descansando.")
                    continue
                if lucro_hoje <= -limite_perda:
                    print(f"   🛑 [{uid}] STOP DIÁRIO ATINGIDO ({lucro_hoje:.2f}%). Segurança ativada.")
                    continue

                limite_rsi = float(user.get("rsi_alvo_compra", 40))
                gatilho_ts = float(user.get("gatilho_trailing", 0.6))
                distancia_ts = float(user.get("distancia_trailing", 0.3))

                # REUNIÃO DA IA
                if time.time() - ultima_reuniao_ia[uid] > tempo_espera_ia:
                    reuniao_com_ia_gestora(user, preco, rsi, volatilidade, marcha, raio_x_book, tendencia_macro, taxa_funding, indice_medo)
                    ultima_reuniao_ia[uid] = time.time()

                # GESTÃO DE ORDENS ABERTAS (TRAILING STOP)
                if uid in operacoes_abertas:
                    op = operacoes_abertas[uid]
                    lucro_pct = ((preco - op["entrada"]) / op["entrada"]) * 100
                    
                    if lucro_pct > op["lucro_maximo"]:
                        op["lucro_maximo"] = lucro_pct

                    vender = False; motivo_venda = ""

                    if lucro_pct >= gatilho_ts and not op["trailing_ativo"]:
                        op["trailing_ativo"] = True
                        print(f"   🛡️ [{uid}] TRAILING STOP ATIVADO! (Gatilho em {gatilho_ts}%). Surfando a alta...")

                    if op["trailing_ativo"]:
                        linha_de_venda = op["lucro_maximo"] - distancia_ts
                        print(f"   🏄‍♂️ [{uid}] Atual: {lucro_pct:.2f}% | Topo: {op['lucro_maximo']:.2f}% | Stop em: {linha_de_venda:.2f}%")
                        if lucro_pct <= linha_de_venda:
                            vender = True; motivo_venda = "Trailing Executado"
                    else:
                        print(f"   👁️ [{uid}] Vigiando... Atual: {lucro_pct:.2f}% | Gatilho em: {gatilho_ts}%")
                        if lucro_pct <= -1.0:
                            vender = True; motivo_venda = "Stop Loss de Proteção"

                    if vender:
                        print(f"   💰 [{uid}] FECHANDO ORDEM ({motivo_venda})! Lucro: {lucro_pct:.2f}%")
                        api_base44("PUT", ENDPOINTS["operacao"], {"preco_saida": preco, "lucro_porcentagem": lucro_pct, "status": "Fechada"}, id_registro=op["id"])
                        atualizar_dashboard_total(user, lucro_pct)
                        if lucro_pct > 0: historico_hora[uid]['reais_vitorias'] += 1
                        else: historico_hora[uid]['reais_derrotas'] += 1
                        del operacoes_abertas[uid]

                # PROCURA NOVAS ENTRADAS
                else:
                    if rsi <= limite_rsi:
                        print(f"   ⚡ [{uid}] COMPRA! RSI: {rsi:.2f}. Modo {modo}.")
                        res = api_base44("POST", ENDPOINTS["operacao"], {
                            "usuario_id": uid, "par_moeda": SYMBOL, "tipo_ordem": "Compra",
                            "categoria_ordem": modo, "preco_entrada": preco, "data_hora": datetime.now().isoformat(), "status": "Aberta"
                        })
                        if res and 'id' in res: operacoes_abertas[uid] = {"id": res['id'], "entrada": preco, "lucro_maximo": 0.0, "trailing_ativo": False}

                    elif rsi <= (limite_rsi + 15):
                        res = api_base44("POST", ENDPOINTS["operacao"], {
                            "usuario_id": uid, "par_moeda": SYMBOL, "tipo_ordem": "Compra",
                            "categoria_ordem": "Fantasma", "preco_entrada": preco, "data_hora": datetime.now().isoformat(), "status": "Aberta"
                        })
                        if res and 'id' in res:
                            ordens_fantasma[uid].append({"id": res['id'], "entrada": preco, "alvo": preco * (1 + (gatilho_ts/100)), "stop": preco * 0.99})

                # ACOMPANHA FANTASMAS
                for f in ordens_fantasma[uid][:]:
                    if preco >= f['alvo']:
                        historico_hora[uid]['fantasma_vitorias'] += 1
                        lucro_pct = ((preco - f['entrada']) / f['entrada']) * 100
                        print(f"   🏁 [{uid}] FANTASMA VITÓRIA: {lucro_pct:.2f}%")
                        api_base44("PUT", ENDPOINTS["operacao"], {"preco_saida": preco, "lucro_porcentagem": lucro_pct, "status": "Fechada"}, id_registro=f['id'])
                        ordens_fantasma[uid].remove(f)
                    
                    elif preco <= f['stop']:
                        historico_hora[uid]['fantasma_derrotas'] += 1
                        lucro_pct = ((preco - f['entrada']) / f['entrada']) * 100
                        print(f"   🏁 [{uid}] FANTASMA PREJUÍZO: {lucro_pct:.2f}%")
                        api_base44("PUT", ENDPOINTS["operacao"], {"preco_saida": preco, "lucro_porcentagem": lucro_pct, "status": "Fechada"}, id_registro=f['id'])
                        ordens_fantasma[uid].remove(f)

            time.sleep(60)
        except Exception as e: print(f"Erro Crítico: {e}"); time.sleep(60)

if __name__ == "__main__":
    iniciar_loop()
