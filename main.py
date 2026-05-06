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

# ==========================================
# 2. FUNÇÕES DE APOIO E BASE44
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

def calcular_rsi_real(exchange):
    """Calcula a força real do mercado na corretora Bybit"""
    try:
        velas = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=15)
        fechamentos = [v[4] for v in velas]
        ganhos, perdas = [], []
        for i in range(1, len(fechamentos)):
            diff = fechamentos[i] - fechamentos[i-1]
            if diff > 0: ganhos.append(diff)
            else: perdas.append(abs(diff))
        media_ganhos = sum(ganhos) / 14 if ganhos else 0
        media_perdas = sum(perdas) / 14 if perdas else 0
        if media_perdas == 0: return 100
        return 100 - (100 / (1 + (media_ganhos / media_perdas)))
    except: return 50

def atualizar_dashboard_total(usuario, lucro_operacao_pct):
    """Atualiza Saldo e Meta Diária no Dashboard Base44"""
    uid = usuario['usuario_id']
    
    # Atualiza Saldo[cite: 2]
    saldos = api_base44("GET", ENDPOINTS["saldo"])
    if saldos:
        reg_saldo = next((s for s in saldos if s['usuario_id'] == uid), None)
        if reg_saldo:
            valor_financeiro = 100 * (lucro_operacao_pct / 100) # Banca simulada de $100
            novo_saldo = float(reg_saldo.get('saldo_demo', 0)) + valor_financeiro
            api_base44("PUT", ENDPOINTS["saldo"], {"saldo_demo": novo_saldo}, id_registro=reg_saldo['id'])

    # Atualiza Progresso Meta[cite: 6]
    lucro_acumulado = float(usuario.get("lucro_hoje_porcentagem") or 0.0) + lucro_operacao_pct
    api_base44("PUT", ENDPOINTS["controle"], {
        "lucro_hoje_porcentagem": lucro_acumulado,
        "lucro_hoje": 100 * (lucro_acumulado / 100)
    }, id_registro=usuario['id'])

# ==========================================
# 3. O CÉREBRO: IA GESTORA
# ==========================================
def reuniao_com_ia_gestora(usuario, preco_atual, rsi_atual):
    uid = usuario.get("usuario_id")
    id_banco = usuario.get("id") 
    rsi_antigo = usuario.get("rsi_alvo_compra", 40)
    hist = historico_hora[uid]
    
    print(f"\n🧠 [DESPERTAR DA IA] Analisando mercado para {uid}...")
    prompt = f"""
    Ativo: {SYMBOL}. Preço: {preco_atual:.2f}. RSI: {rsi_atual:.2f}. Alvo Antigo: {rsi_antigo}.
    Vitórias Reais: {hist['reais_vitorias']} | Derrotas: {hist['reais_derrotas']}
    Vitórias Fantasmas: {hist['fantasma_vitorias']} | Derrotas: {hist['fantasma_derrotas']}
    Ajuste o 'rsi_alvo_compra'. Responda JSON: {{"rsi_alvo_compra": 40, "observacao_ia": "Motivo..."}}
    """
    try:
        res = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        nova_regra = json.loads(res.text.replace("```json", "").replace("```", "").strip())
        api_base44("PUT", ENDPOINTS["controle"], nova_regra, id_registro=id_banco)
        print(f"✅ [DECISÃO IA] Novo RSI: {nova_regra['rsi_alvo_compra']} | Motivo: {nova_regra['observacao_ia']}\n")
        historico_hora[uid] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
    except Exception as e: print(f"❌ [ERRO IA]: {e}")

# ==========================================
# 4. O OPERÁRIO: LOOP PRINCIPAL
# ==========================================
def iniciar_loop():
    print("🚀 MIRAQUANTIA ONLINE - MODO VERBOSE (LOGS DETALHADOS) ATIVADO")
    global ultima_reuniao_ia, ordens_fantasma, historico_hora, operacoes_abertas
    
    while True:
        try:
            configs = api_base44("GET", ENDPOINTS["controle"])
            if not configs: time.sleep(60); continue

            ex = ccxt.bybit()
            preco = ex.fetch_ticker(SYMBOL)['last']
            rsi = calcular_rsi_real(ex)
            hora_atual = datetime.now().strftime('%H:%M:%S')
            
            print(f"[{hora_atual}] 📊 MERCADO | Preço: {preco} | RSI: {rsi:.2f}")

            for user in configs:
                uid = user.get("usuario_id")
                modo = str(user.get("modo_operacao", "Demo")).capitalize()
                meta = float(user.get("meta_diaria_porcentagem") or 2.0)
                lucro_hoje = float(user.get("lucro_hoje_porcentagem") or 0.0)

                if not user.get("status_bot"): continue
                
                if uid not in ultima_reuniao_ia: ultima_reuniao_ia[uid] = 0
                if uid not in ordens_fantasma: ordens_fantasma[uid] = []
                if uid not in historico_hora: historico_hora[uid] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}

                # 1. Verifica se atingiu a meta diária[cite: 6]
                if lucro_hoje >= meta:
                    print(f"   🛑 [{uid}] IGNORANDO: Meta diária de {meta}% já atingida ({lucro_hoje:.2f}%).")
                    continue

                # 2. Chama a IA a cada 25 minutos
                if time.time() - ultima_reuniao_ia[uid] > 1500:
                    reuniao_com_ia_gestora(user, preco, rsi)
                    ultima_reuniao_ia[uid] = time.time()
                    user['rsi_alvo_compra'] = api_base44("GET", ENDPOINTS["controle"], id_registro=user['id']).get("rsi_alvo_compra", 40)

                limite_rsi = user.get("rsi_alvo_compra", 40)

                # 3. Acompanha Ordens Abertas (Real/Demo)
                if uid in operacoes_abertas:
                    op = operacoes_abertas[uid]
                    lucro_pct = ((preco - op["entrada"]) / op["entrada"]) * 100
                    print(f"   👁️ [{uid}] VIGIANDO ORDEM | Entrada: {op['entrada']} | Atual: {preco} | Lucro: {lucro_pct:.2f}%")
                    
                    if lucro_pct >= 0.5 or lucro_pct <= -1.0: # Alvos de Take Profit e Stop Loss
                        print(f"   💰 [{uid}] FECHANDO ORDEM! Lucro: {lucro_pct:.2f}%")
                        api_base44("PUT", ENDPOINTS["operacao"], {
                            "preco_saida": preco, "lucro_porcentagem": lucro_pct, "status": "Fechada"
                        }, id_registro=op["id"])
                        atualizar_dashboard_total(user, lucro_pct)
                        if lucro_pct > 0: historico_hora[uid]['reais_vitorias'] += 1
                        else: historico_hora[uid]['reais_derrotas'] += 1
                        del operacoes_abertas[uid]

                # 4. Procura Novas Entradas
                else:
                    if rsi <= limite_rsi:
                        print(f"   ⚡ [{uid}] SINAL DE COMPRA! RSI bateu {limite_rsi}. Executando modo {modo}.")
                        res = api_base44("POST", ENDPOINTS["operacao"], {
                            "usuario_id": uid, "par_moeda": SYMBOL, "tipo_ordem": "Compra",
                            "categoria_ordem": modo, "preco_entrada": preco,
                            "data_hora": datetime.now().isoformat(), "status": "Aberta"
                        })
                        if res: operacoes_abertas[uid] = {"id": res['id'], "entrada": preco}

                    elif rsi <= (limite_rsi + 15):
                        print(f"   👻 [{uid}] RSI perto do alvo ({rsi:.2f}). Criando ORDEM FANTASMA para estudo.")
                        ordens_fantasma[uid].append({"entrada": preco, "alvo": preco * 1.005, "stop": preco * 0.99})
                        api_base44("POST", ENDPOINTS["operacao"], {
                            "usuario_id": uid, "par_moeda": SYMBOL, "tipo_ordem": "Compra",
                            "categoria_ordem": "Fantasma", "preco_entrada": preco,
                            "data_hora": datetime.now().isoformat(), "status": "Aberta"
                        })
                    else:
                        print(f"   ⏳ [{uid}] IGNORANDO: RSI ({rsi:.2f}) muito acima da meta ({limite_rsi}). Mercado esticado.")

                # 5. Acompanha Fantasmas (Apenas RAM)
                for f in ordens_fantasma[uid][:]:
                    if preco >= f['alvo']:
                        historico_hora[uid]['fantasma_vitorias'] += 1
                        ordens_fantasma[uid].remove(f)
                    elif preco <= f['stop']:
                        historico_hora[uid]['fantasma_derrotas'] += 1
                        ordens_fantasma[uid].remove(f)

            time.sleep(60)
        except Exception as e:
            print(f"Erro Crítico: {e}"); time.sleep(60)

if __name__ == "__main__":
    iniciar_loop()
