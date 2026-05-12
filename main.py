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
cooldown_moedas = {} 
consecutivas_perdas = {} # 🛡️ RASTREADOR DE DERROTAS PARA ALAVANCAGEM
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
        velas = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=250)
        fechamentos = [v[4] for v in velas]
        preco_atual = fechamentos[-1]
        
        multiplicador = 2 / (200 + 1)
        ema_200 = fechamentos[0]
        for preco in fechamentos:
            ema_200 = (preco - ema_200) * multiplicador + ema_200
        
        tendencia_macro = "ALTA" if preco_atual > ema_200 else "BAIXA"

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
    - Se o RSI cair abaixo de 15, considere exaustão total.
    
    Responda APENAS JSON:
    {{
      "direcao_operacao": "Compra" ou "Venda",
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
        
        prefixo = "btc" if symbol == "BTC/USDT" else "eth" if symbol == "ETH/USDT" else "sol"
        nova_regra[f"observacao_{prefixo}"] = f"⏱️ [{hora_reuniao}] {nova_regra.get('observacao_ia', '')}"
        nova_regra[f"status_{prefixo}"] = nova_regra.get("status_mercado", f"Atualizado {hora_reuniao}")
        nova_regra["rsi_alvo_compra"] = nova_regra["rsi_alvo"] 
        
        if "observacao_ia" in nova_regra: del nova_regra["observacao_ia"]
        if "status_mercado" in nova_regra: del nova_regra["status_mercado"]
        
        api_base44("PUT", ENDPOINTS["controle"], nova_regra, id_registro=id_banco)
    except: pass

def auditoria_saida_ia(symbol, preco_atual, rsi_atual, lucro_atual, tendencia_macro):
    prompt = f"""
    A operação em {symbol} está ABERTA e com LUCRO ALTO de +{lucro_atual:.2f}%.
    Preço atual: {preco_atual:.2f}. RSI: {rsi_atual:.2f}. Tendência Macro: {tendencia_macro}.
    
    MÓDULO DE SAÍDA DE EMERGÊNCIA:
    - O objetivo é tentar arrancar o máximo de lucro antes de reverter.
    - Se o RSI mostrar que a força da tendência acabou (exaustão), recomende "FECHAR".
    - Se o movimento ainda tem força para continuar, recomende "SEGURAR".
    
    Responda APENAS JSON:
    {{
      "decisao": "FECHAR" ou "SEGURAR",
      "motivo": "justificativa curta"
    }}
    """
    try:
        res = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        analise = json.loads(res.text.replace("```json", "").replace("```", "").strip())
        return analise.get("decisao", "SEGURAR"), analise.get("motivo", "Força do movimento")
    except:
        return "SEGURAR", "Falha na API, mantendo trailing normal."

# ==========================================
# 4. O OPERÁRIO: LOOP COM MAXIMIZADOR E ALAVANCAGEM
# ==========================================
def iniciar_loop():
    print("🚀 MIRAQUANTIA SCALPER - MODO STOP LARGO (-10%) E ALAVANCAGEM DE RECUPERAÇÃO")
    global ultima_reuniao_ia, operacoes_abertas, data_operacao_usuario, cooldown_moedas, consecutivas_perdas
    
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
                
                if uid not in data_operacao_usuario or data_operacao_usuario[uid] != hoje_data:
                    reconciliar_lucro_diario(uid, user['id'], hoje_data, todas_ops_db)
                    data_operacao_usuario[uid] = hoje_data
                
                lucro_hoje = float(user.get("lucro_hoje_porcentagem") or 0.0)
                if lucro_hoje >= float(user.get("meta_diaria_porcentagem") or 2.0): continue

                reg_saldo = next((s for s in saldos if s['usuario_id'] == uid), None) if saldos else None
                saldo_total = float(reg_saldo.get('saldo_demo') or 100.0)
                capital_preso = sum(op["capital_alocado"] for k, op in operacoes_abertas.items() if k.startswith(f"{uid}_"))
                saldo_livre = saldo_total - capital_preso
                
                capital_op = float(user.get("capital_por_operacao") or (saldo_livre * 0.30))

                for symbol in MOEDAS_ATIVAS:
                    preco, rsi, volatilidade, tendencia_macro = ler_mercado(ex, symbol)
                    if preco is None: continue
                    
                    mem_key = f"{uid}_{symbol}"
                    if mem_key not in consecutivas_perdas: consecutivas_perdas[mem_key] = 0
                    
                    print(f"   ► {symbol} | RSI: {rsi:.1f} | Tendência: {tendencia_macro} | Perdas Seguidas: {consecutivas_perdas[mem_key]}")

                    if mem_key in cooldown_moedas:
                        if time.time() < cooldown_moedas[mem_key]: continue

                    # GESTÃO DE ORDENS ABERTAS
                    if mem_key in operacoes_abertas:
                        op = operacoes_abertas[mem_key]
                        if op["id"] in ops_fechadas_db:
                            # Intervenção Manual: assume lucro/perda e apaga
                            ordem_db = next((x for x in todas_ops_db if x['id'] == op['id']), None)
                            if ordem_db:
                                pct_final = float(ordem_db.get("lucro_porcentagem") or 0.0)
                                if pct_final > 0: consecutivas_perdas[mem_key] = 0
                                else: consecutivas_perdas[mem_key] += 1
                            del operacoes_abertas[mem_key]; continue
                        
                        lucro_pct = ((preco - op["entrada"]) / op["entrada"]) * 100 if op["tipo_ordem"] == "Compra" else ((op["entrada"] - preco) / op["entrada"]) * 100
                        if lucro_pct > op["lucro_maximo"]: op["lucro_maximo"] = lucro_pct
                        
                        api_base44("PUT", ENDPOINTS["operacao"], {"preco_saida": preco, "lucro_porcentagem": lucro_pct, "lucro_financeiro": op["capital_alocado"] * (lucro_pct / 100)}, id_registro=op["id"])

                        tempo_aberta = time.time() - op["hora_criacao"]
                        vender = False; motivo = ""
                        
                        gatilho_base = 0.15 if fds_ativo else float(user.get("gatilho_trailing", 0.4))
                        distancia_ts = 0.05 if fds_ativo else float(user.get("distancia_trailing", 0.2))

                        if lucro_pct >= gatilho_base and not op.get("auditoria_ia_feita"):
                            print(f"   🚨 [{symbol}] Lucro de +{lucro_pct:.2f}%! Auditoria IA...")
                            decisao_ia, motivo_ia = auditoria_saida_ia(symbol, preco, rsi, lucro_pct, tendencia_macro)
                            op["auditoria_ia_feita"] = True
                            
                            if decisao_ia == "FECHAR": vender = True; motivo = f"IA Topo/Fundo: {motivo_ia}"
                            else: op["trailing_afrouxado"] = True

                        if op.get("trailing_afrouxado"): distancia_ts = distancia_ts * 2

                        if not vender:
                            if tempo_aberta > 600 and lucro_pct >= 0.05 and lucro_pct < gatilho_base: vender = True; motivo = "Fuga por Estagnação"
                            elif lucro_pct >= gatilho_base: op["trailing_ativo"] = True
                            
                            if op.get("trailing_ativo") and lucro_pct <= (op["lucro_maximo"] - distancia_ts): vender = True; motivo = "Trailing Stop Executado"
                            
                            # 🛡️ NOVO STOP LOSS DRÁSTICO DE -10%
                            elif lucro_pct <= -10.0: 
                                vender = True; motivo = "Stop Loss Drástico (-10%)"
                                cooldown_moedas[mem_key] = time.time() + 1200 

                        if vender:
                            # 🛡️ ATUALIZA O RASTREADOR DE DERROTAS
                            if lucro_pct > 0:
                                consecutivas_perdas[mem_key] = 0
                            else:
                                consecutivas_perdas[mem_key] += 1
                                
                            api_base44("PUT", ENDPOINTS["operacao"], {"preco_saida": preco, "lucro_porcentagem": lucro_pct, "status": "Fechada", "motivo_fechamento": motivo}, id_registro=op["id"])
                            atualizar_dashboard_total(user, lucro_pct, op["capital_alocado"] * (lucro_pct / 100))
                            del operacoes_abertas[mem_key]

                    # ENTRADA SNIPER & MODO RECUPERAÇÃO
                    else:
                        direcao = user.get("direcao_operacao", "Compra")
                        
                        # 🛡️ LÓGICA DO MODO RECUPERAÇÃO (ALAVANCAGEM)
                        em_recuperacao = consecutivas_perdas[mem_key] >= 3
                        
                        rsi_compra_alvo = 25 if em_recuperacao else 30
                        rsi_venda_alvo = 75 if em_recuperacao else 70
                        
                        # Se estiver em recuperação, dobra o capital (limitado ao saldo livre)
                        capital_final = min(capital_op * 2.0, saldo_livre) if em_recuperacao else capital_op

                        pode_comprar = (tendencia_macro == "ALTA" and 15 < rsi < rsi_compra_alvo)
                        pode_vender = (tendencia_macro == "BAIXA" and rsi > rsi_venda_alvo)

                        if (direcao == "Compra" and pode_comprar) or (direcao == "Venda" and pode_vender):
                            if em_recuperacao:
                                print(f"   ⚠️ [{symbol}] MODO ALAVANCAGEM ATIVADO! Entrada cirúrgica para recuperar perdas. Capital: ${capital_final:.2f}")
                                
                            res = api_base44("POST", ENDPOINTS["operacao"], {"usuario_id": uid, "par_moeda": symbol, "tipo_ordem": direcao, "categoria_ordem": modo, "preco_entrada": preco, "data_hora": agora_br.isoformat(), "status": "Aberta", "capital_alocado": capital_final})
                            if res and 'id' in res: 
                                operacoes_abertas[mem_key] = {"id": res['id'], "entrada": preco, "lucro_maximo": 0.0, "trailing_ativo": False, "auditoria_ia_feita": False, "capital_alocado": capital_final, "tipo_ordem": direcao, "hora_criacao": time.time()}

                    if time.time() - ultima_reuniao_ia.get(mem_key, 0) > 900:
                        reuniao_com_ia_gestora(user, symbol, preco, rsi, volatilidade, "TURBO", tendencia_macro)
                        ultima_reuniao_ia[mem_key] = time.time()

            time.sleep(10)
        except Exception as e:
            print(f"⚠️ Erro Loop: {e}"); time.sleep(10)

if __name__ == "__main__":
    iniciar_loop()
