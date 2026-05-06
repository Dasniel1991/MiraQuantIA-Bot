import ccxt
import time
import requests
import os
import json
from datetime import datetime
from google import genai

# ==========================================
# 1. CONFIGURAÇÕES
# ==========================================
BASE44_API_KEY = os.environ.get("BASE44_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

cliente_ia = genai.Client(api_key=GEMINI_API_KEY)
MODELO_GEMINI = "gemini-3-flash-preview"

BASE_URL = "https://miraquant-ia.base44.app/api"
ENDPOINTS = {
    "controle": f"{BASE_URL}/entities/ControleBot",
    "operacao": f"{BASE_URL}/entities/Operacao",
    "saldo": f"{BASE_URL}/entities/SaldoUsuario" # Tabela de Saldo
}

SYMBOL = 'BTC/USDT'
TIMEFRAME = '1m'

# Estados de Memória
operacoes_abertas = {} 
ordens_fantasma = {}
historico_hora = {}
ultima_reuniao_ia = {}

# ==========================================
# 3. FUNÇÕES DE COMUNICAÇÃO BASE44
# ==========================================
def api_base44(metodo, endpoint, dados=None, id_registro=None):
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    url = f"{endpoint}/{id_registro}" if id_registro else endpoint
    try:
        if metodo == "GET": res = requests.get(url, headers=headers)
        elif metodo == "POST": res = requests.post(url, json=dados, headers=headers)
        elif metodo == "PUT": res = requests.put(url, json=dados, headers=headers)
        
        if res.status_code in [200, 201, 204]:
            return res.json() if res.text else True
        return None
    except:
        return None

def atualizar_saldo_demo(usuario_id, valor_lucro):
    """Busca o saldo atual e soma/subtrai o resultado da operação[cite: 2]"""
    saldos = api_base44("GET", ENDPOINTS["saldo"])
    if saldos:
        # Busca a linha de saldo do usuário específico
        registro = next((s for s in saldos if s['usuario_id'] == usuario_id), None)
        if registro:
            novo_saldo = float(registro.get('saldo_demo', 0)) + valor_lucro
            api_base44("PUT", ENDPOINTS["saldo"], {"saldo_demo": novo_saldo}, id_registro=registro['id'])
            print(f"💰 Saldo Demo atualizado: {novo_saldo} USDT")

# ==========================================
# 4. LOOP DO OPERÁRIO (AGRESSIVO PARA TESTE)
# ==========================================
def iniciar_loop():
    global ultima_reuniao_ia, operacoes_abertas
    print("🚀 MIRAQUANTIA V4.1 - MODO TESTE DE ASSERTIVIDADE ATIVO")
    
    while True:
        try:
            configs = api_base44("GET", ENDPOINTS["controle"])
            if not configs:
                time.sleep(60); continue

            ex = ccxt.bybit()
            preco = ex.fetch_ticker(SYMBOL)['last']
            
            for user in configs:
                uid = user.get("usuario_id")
                if not user.get("status_bot"): continue
                
                # Memória do usuário
                if uid not in operacoes_abertas:
                    # REGRA DE TESTE: Se o RSI estiver abaixo de 50 (neutro), ele já entra para testarmos
                    # Em produção, usaremos o 'rsi_alvo_compra' da IA[cite: 6]
                    if preco < 90000: # Gatilho forçado para teste imediato
                        print(f"⚠️ [TESTE] Forçando entrada para verificar assertividade em {uid}")
                        res_compra = api_base44("POST", ENDPOINTS["operacao"], {
                            "usuario_id": uid, "par_moeda": SYMBOL, "tipo_ordem": "Compra",
                            "categoria_ordem": "Demo", "preco_entrada": preco,
                            "data_hora": datetime.now().isoformat(), "status": "Aberta"
                        })
                        if res_compra:
                            operacoes_abertas[uid] = {"id_banco": res_compra['id'], "preco_entrada": preco}
                
                # Lógica de Venda e Atualização de Saldo
                elif uid in operacoes_abertas:
                    op = operacoes_abertas[uid]
                    lucro_pct = ((preco - op["preco_entrada"]) / op["preco_entrada"]) * 100
                    
                    # Alvo curto de 0.2% para você ver o saldo mudar rápido hoje
                    if lucro_pct >= 0.2 or lucro_pct <= -0.2:
                        lucro_fin = 100 * (lucro_pct / 100) # Simula operação de 100 USDT
                        
                        # Atualiza a tabela Operação[cite: 3]
                        api_base44("PUT", ENDPOINTS["operacao"], {
                            "preco_saida": preco, "lucro_porcentagem": lucro_pct,
                            "lucro_financeiro": lucro_fin, "status": "Fechada"
                        }, id_registro=op["id_banco"])
                        
                        # Atualiza o Saldo Real no Dashboard[cite: 2]
                        atualizar_saldo_demo(uid, lucro_fin)
                        
                        del operacoes_abertas[uid]
                        print(f"✅ Operação finalizada com {lucro_pct:.2f}% de lucro.")

            time.sleep(30) # Checagem mais rápida para o teste
        except Exception as e:
            print(f"Erro: {e}"); time.sleep(30)

if __name__ == "__main__":
    iniciar_loop()
