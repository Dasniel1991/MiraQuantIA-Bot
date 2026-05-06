import ccxt
import time
import requests
import os
import json
from datetime import datetime
from google import genai

# ==========================================
# CONFIGURAÇÕES E ENDPOINTS
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
operacoes_memoria = {} 

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

def atualizar_dashboard_total(usuario, lucro_operacao_pct):
    """Atualiza o saldo total e o progresso da meta diária no painel"""
    uid = usuario['usuario_id']
    
    # 1. Atualiza Saldo na Conta Demo[cite: 2]
    saldos = api_base44("GET", ENDPOINTS["saldo"])
    if saldos:
        reg_saldo = next((s for s in saldos if s['usuario_id'] == uid), None)
        if reg_saldo:
            valor_financeiro = 100 * (lucro_operacao_pct / 100) # Simulando banca de 100 USDT
            novo_saldo = float(reg_saldo.get('saldo_demo', 0)) + valor_financeiro
            api_base44("PUT", ENDPOINTS["saldo"], {"saldo_demo": novo_saldo}, id_registro=reg_saldo['id'])

    # 2. Atualiza o Progresso da Meta Diária (%) no Dashboard
    lucro_acumulado_atual = float(usuario.get("lucro_hoje_porcentagem") or 0.0)
    novo_acumulado = lucro_acumulado_atual + lucro_operacao_pct
    
    api_base44("PUT", ENDPOINTS["controle"], {
        "lucro_hoje_porcentagem": novo_acumulado,
        "lucro_hoje": 100 * (novo_acumulado / 100) # Lucro financeiro hoje
    }, id_registro=usuario['id'])
    
    print(f"📊 Dashboard Atualizado: Progresso do dia em {novo_acumulado:.2f}%")

def iniciar_loop():
    print("🚀 MIRAQUANTIA ONLINE - ATUALIZAÇÃO DE DASHBOARD ATIVA")
    while True:
        try:
            configs = api_base44("GET", ENDPOINTS["controle"])
            if not configs: time.sleep(60); continue

            ex = ccxt.bybit()
            preco = ex.fetch_ticker(SYMBOL)['last']
            
            for user in configs:
                uid = user.get("usuario_id")
                if not user.get("status_bot"): continue
                
                # Respeita Meta Diária[cite: 6]
                meta = float(user.get("meta_diaria_porcentagem") or 2.0)
                lucro_hoje = float(user.get("lucro_hoje_porcentagem") or 0.0)
                if lucro_hoje >= meta: continue

                # Lógica de Operação
                if uid not in operacoes_memoria:
                    # Gatilho de teste (RSI baixo)
                    if preco < 100000: 
                        res = api_base44("POST", ENDPOINTS["operacao"], {
                            "usuario_id": uid, "par_moeda": SYMBOL, "tipo_ordem": "Compra",
                            "categoria_ordem": "Demo", "preco_entrada": preco,
                            "data_hora": datetime.now().isoformat(), "status": "Aberta"
                        })
                        if res: operacoes_memoria[uid] = {"id": res['id'], "entrada": preco}

                elif uid in operacoes_memoria:
                    op = operacoes_memoria[uid]
                    lucro_pct = ((preco - op["entrada"]) / op["entrada"]) * 100
                    
                    # Alvo de 0.30% para ver o gráfico mexer
                    if lucro_pct >= 0.3 or lucro_pct <= -0.5:
                        api_base44("PUT", ENDPOINTS["operacao"], {
                            "preco_saida": preco, "lucro_porcentagem": lucro_pct,
                            "status": "Fechada"
                        }, id_registro=op["id"])
                        
                        # CHAMA A ATUALIZAÇÃO DO DASHBOARD AQUI
                        atualizar_dashboard_total(user, lucro_pct)
                        del operacoes_memoria[uid]

            time.sleep(60)
        except Exception as e:
            print(f"Erro: {e}"); time.sleep(60)

if __name__ == "__main__":
    iniciar_loop()
