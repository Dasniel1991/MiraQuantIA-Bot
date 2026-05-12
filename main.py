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
cooldown_moedas = {} # 🛡️ Filtro de Cooldown
timestamps_ia = {'BTC/USDT': '--:--:--', 'ETH/USDT': '--:--:--', 'SOL/USDT': '--:--:--'}

# ==========================================
# 2. FUNÇÕES DE APOIO E LEITURA DE MERCADO
# ==========================================
def obter_data_hora_br():
    agora_utc = datetime.utcnow()
    return agora_utc - timedelta(hours=3)

def is_fim_de_semana():
    agora = obter_data_hora_br()
    dia_semana = agora.weekday() 
    if dia_semana == 5 or dia_semana == 6: return True
    if dia_semana == 4 and agora.hour >= 20: return True
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
        print(f"⚠️ Erro Base44: {e}")
        return None

def ler_mercado(exchange, symbol):
    try:
        # Puxamos mais velas (250) para calcular a EMA 200 (Tendência Macro)
        velas = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=250)
        fechamentos = [v[4] for v in velas]
        preco_atual = fechamentos[-1]
        
        # 1. CÁLCULO EMA 200 (FILTRO DE TENDÊNCIA)
        multiplicador = 2 / (200 + 1)
        ema_200 = fechamentos[0]
        for preco in fechamentos:
            ema_200 = (preco - ema_200) * multiplicador + ema_200
        
        tendencia_macro = "ALTA" if preco_atual > ema_200 else "BAIXA"

        # 2. CÁLCULO VOLATILIDADE E RSI (Últimos 14 períodos)
        maximas = [v[2] for v in velas[-15:]]
        minimas = [v[3] for v in velas[-15:]]
        volatilidade = ((max(maximas) - min(minimas)) / min(minimas)) * 100

        ganhos, perdas = [], []
        for i in range(len(fechamentos)-14, len(fechamentos)):
            diff = fechamentos[i] - fechamentos[i-1]
            if diff > 0: ganhos.append(diff)
            else: perdas.append(abs(diff))
        media_ganhos = sum(ganhos) / 14 if ganhos else 0
        media_perdas = sum(perdas) / 14 if perdas else 0
        rsi = 100 if media_perdas == 0 else 100 - (100 / (1 + (media_ganhos / media_perdas)))
            
        return preco_atual, rsi, volatilidade, tendencia_macro
    except Exception as e:
        print(f"Erro leitura ({symbol}): {e}")
        return None, None, None, None

def reconciliar_lucro_diario(uid, id_banco, hoje_data, todas_ops_db):
    lucro_real = 0.0
    for op in todas_ops_db:
        if op.get("usuario_id") == uid and op.get("status") == "Fechada" and op.get("categoria_ordem") != "Fantasma":
            if hoje_data in str(op.get("data_hora", "")):
                lucro_real += float(op.get("lucro_porcentagem") or 0.0)
    api_base44("PUT", ENDPOINTS["controle"], {"lucro_hoje_porcentagem": lucro_real, "lucro_hoje": 100 * (lucro_real / 100)}, id_registro=id_banco)
    return lucro_real

def atualizar_dashboard_total(usuario, lucro_operacao_pct, valor_financeiro):
    uid = usuario['usuario_id']
    saldos = api_base44("GET", ENDPOINTS["saldo"])
    if saldos:
        reg_saldo = next((s for s in saldos if s['usuario_id'] == uid), None)
        if reg_saldo:
            novo_saldo = float(reg_saldo.get('saldo_demo') or 0.0) + valor_financeiro
            api_base44("PUT", ENDPOINTS["saldo"], {"saldo_demo": novo_saldo}, id_registro=reg_saldo['id'])

    lucro_acumulado = float(usuario.get("lucro_hoje_porcentagem") or 0.0) + lucro_operacao_pct
    api_base44("PUT", ENDPOINTS["controle"], {"lucro_hoje_porcentagem": lucro_acumulado, "lucro_hoje": 100 * (lucro_acumulado / 100)}, id_registro=usuario['id'])

# ==========================================
# 3. O CÉREBRO: IA + ESTRATÉGIA
# ==========================================
def reuniao_com_ia_gestora(usuario, symbol, preco_atual, rsi_atual, volatilidade, marcha, tendencia_macro):
    global timestamps_ia
    id_banco = usuario.get("id") 
    mem_key = f"{usuario.get('usuario_id')}_{symbol}"
    
    prompt = f"""
    Ativo: {symbol}. Preço: {preco_atual:.2f}. RSI: {rsi_atual:.2f}. Volatilidade: {volatilidade:.2f}% ({marcha}).
    TENDÊNCIA MACRO (EMA 200): O preço está em tendência de {tendencia_macro}.
    
    REGRAS DE OURO:
    - Se tendência é BAIXA, priorize RSI alto para VENDA (> 70).
    - Se tendência é ALTA, priorize RSI baixo para COMPRA (< 30).
    - Se o RSI cair abaixo de 20, considere exaustão total e não tente "pecar a faca".
    
    Responda APENAS JSON:
    {{
      "direcao_operacao": "Compra ou Venda",
      "rsi_alvo": 30, 
      "gatilho_trailing": 0.4, 
      "distancia_trailing": 0.2, 
      "status_mercado": "[{symbol}] 🔍 Analisando...",
      "observacao_ia": "Tese estratégica..."
    }}
    """
    try:
        res = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        nova_regra = json.loads(res.text.replace("```json", "").replace("```", "").strip())
        hora_reuniao = obter_data_hora_br().strftime('%H:%M:%S')
        timestamps_ia[symbol] = hora_reuniao
        
        nova_regra["observacao_ia"] = f"⏱️ [{hora_reuniao}] {nova_regra.get('observacao_ia', '')}"
        nova_regra["rsi_alvo_compra"] = nova_regra["rsi_alvo"] 
        api_base44("PUT", ENDPOINTS["controle"], nova_regra, id_registro=id_banco)
    except: pass

# ==========================================
# 4. O OPERÁRIO: LOOP COM NOVOS ESCUDOS
# ==========================================
def iniciar_loop():
    print("🚀 MIRAQUANTIA SCALPER - MODO SNIPER PROFISSIONAL ATIVADO")
    global ultima_reuniao_ia, operacoes_abertas, data_operacao_usuario, cooldown_moedas
    
    while True:
        try:
            configs = api_base44("GET", ENDPOINTS["controle"])
            saldos = api_base44("GET", ENDPOINTS["saldo"]) 
            todas_ops_db = api_base44("GET", ENDPOINTS["operacao"])
            if not configs: time.sleep(10); continue

            ops_fechadas_db = [op['id'] for op in todas_ops_db if op.get("status") == "Fechada"] if todas_ops_db else []
            ex = ccxt.bybit()
            agora_br = obter_data_hora_br()
            hoje_data = agora_br.strftime('%Y-%m-%d')
            fds_ativo = is_fim_de_semana()
            
            print(f"\n[{agora_br.strftime('%H:%M:%S')}] 🔭 ESCANEANDO O MERCADO...")

            for user in configs:
                uid = user.get("usuario_id")
                if not user.get("status_bot"): continue
                
                # RESET E RECONCILIAÇÃO
                if uid not in data_operacao_usuario or data_operacao_usuario[uid] != hoje_data:
                    reconciliar_lucro_diario(uid, user['id'], hoje_data, todas_ops_db)
                    data_operacao_usuario[uid] = hoje_data
                
                lucro_hoje = float(user.get("lucro_hoje_porcentagem") or 0.0)
                if lucro_hoje >= float(user.get("meta_diaria_porcentagem") or 2.0): continue

                reg_saldo = next((s for s in saldos if s['usuario_id'] == uid), None) if saldos else None
                saldo_total = float(reg_saldo.get('saldo_demo') or 100.0)
                capital_op = float(user.get("capital_por_operacao") or (saldo_total * 0.30))

                for symbol in MOEDAS_ATIVAS:
                    preco, rsi, volatilidade, tendencia_macro = ler_mercado(ex, symbol)
                    if preco is None: continue
                    
                    mem_key = f"{uid}_{symbol}"
                    print(f"   ► {symbol} | RSI: {rsi:.1f} | Tendência: {tendencia_macro}")

                    # -------------------------------------------------------------
                    # 🛡️ FILTRO 1: COOLDOWN (20 MINUTOS APÓS PERDA)
                    # -------------------------------------------------------------
                    if mem_key in cooldown_moedas:
                        if time.time() < cooldown_moedas[mem_key]:
                            minutos_restantes = int((cooldown_moedas[mem_key] - time.time()) / 60)
                            print(f"   ⏳ {symbol} em Cooldown... ({minutos_restantes} min)")
                            continue

                    # GESTÃO DE ORDENS ABERTAS
                    if mem_key in operacoes_abertas:
                        op = operacoes_abertas[mem_key]
                        if op["id"] in ops_fechadas_db:
                            del operacoes_abertas[mem_key]; continue
                        
                        lucro_pct = ((preco - op["entrada"]) / op["entrada"]) * 100 if op["tipo_ordem"] == "Compra" else ((op["entrada"] - preco) / op["entrada"]) * 100
                        if lucro_pct > op["lucro_maximo"]: op["lucro_maximo"] = lucro_pct
                        
                        # ATUALIZAÇÃO AO VIVO
                        api_base44("PUT", ENDPOINTS["operacao"], {"preco_saida": preco, "lucro_porcentagem": lucro_pct, "lucro_financeiro": op["capital_alocado"] * (lucro_pct / 100)}, id_registro=op["id"])

                        # LÓGICA DE FUGA DINÂMICA
                        tempo_aberta = time.time() - op["hora_criacao"]
                        vender = False; motivo = ""
                        
                        # 🛡️ FILTRO 2: FUGA POR TEMPO/EXAUSTÃO
                        if tempo_aberta > 600 and lucro_pct >= 0.05: vender = True; motivo = "Fuga por Estagnação"
                        elif lucro_pct >= (0.15 if fds_ativo else float(user.get("gatilho_trailing", 0.4))):
                            op["trailing_ativo"] = True
                        
                        if op["trailing_ativo"] and lucro_pct <= (op["lucro_maximo"] - 0.2): vender = True; motivo = "Trailing Stop"
                        elif lucro_pct <= -0.35: 
                            vender = True; motivo = "Stop Loss de Proteção"
                            # ATIVA COOLDOWN DE 20 MINUTOS SE PERDER
                            cooldown_moedas[mem_key] = time.time() + 1200 

                        if vender:
                            api_base44("PUT", ENDPOINTS["operacao"], {"preco_saida": preco, "lucro_porcentagem": lucro_pct, "status": "Fechada", "motivo_fechamento": motivo}, id_registro=op["id"])
                            atualizar_dashboard_total(user, lucro_pct, op["capital_alocado"] * (lucro_pct / 100))
                            del operacoes_abertas[mem_key]

                    # -------------------------------------------------------------
                    # 🛡️ FILTRO 3: ENTRADA SNIPER (MACRO + RSI EXTREMO)
                    # -------------------------------------------------------------
                    else:
                        direcao = user.get("direcao_operacao", "Compra")
                        # Só entra se o RSI for extremo (< 30 para Compra ou > 70 para Venda)
                        # E se não estiver em "exaustão perigosa" (RSI < 20 para Compra)
                        pode_comprar = (tendencia_macro == "ALTA" and 22 < rsi < 30)
                        pode_vender = (tendencia_macro == "BAIXA" and rsi > 70)

                        if (direcao == "Compra" and pode_comprar) or (direcao == "Venda" and pode_vender):
                            res = api_base44("POST", ENDPOINTS["operacao"], {"usuario_id": uid, "par_moeda": symbol, "tipo_ordem": direcao, "categoria_ordem": modo, "preco_entrada": preco, "data_hora": agora_br.isoformat(), "status": "Aberta"})
                            if res and 'id' in res: 
                                operacoes_abertas[mem_key] = {"id": res['id'], "entrada": preco, "lucro_maximo": 0.0, "trailing_ativo": False, "capital_alocado": capital_op, "tipo_ordem": direcao, "hora_criacao": time.time()}

                    # REUNIÃO IA
                    if time.time() - ultima_reuniao_ia.get(mem_key, 0) > 900:
                        reuniao_com_ia_gestora(user, symbol, preco, rsi, volatilidade, "TURBO", tendencia_macro)
                        ultima_reuniao_ia[mem_key] = time.time()

            time.sleep(10)
        except Exception as e:
            print(f"⚠️ Erro Loop: {e}"); time.sleep(10)

if __name__ == "__main__":
    iniciar_loop()
