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

def atualizar_progresso_diario(usuario, lucro_operacao_pct):
    """Atualiza o lucro acumulado no dia e o saldo total[cite: 2, 6]"""
    # 1. Atualiza Saldo Demo[cite: 2]
    saldos = api_base44("GET", ENDPOINTS["saldo"])
    if saldos:
        reg_saldo = next((s for s in saldos if s['usuario_id'] == usuario['usuario_id']), None)
        if reg_saldo:
            novo_saldo = float(reg_saldo.get('saldo_demo', 0)) + (100 * (lucro_operacao_pct/100))
            api_base44("PUT", ENDPOINTS["saldo"], {"saldo_demo": novo_saldo}, id_registro=reg_saldo['id'])

    # 2. Atualiza Lucro Hoje no Controle
    lucro_atual = float(usuario.get("lucro_hoje_porcentagem") or 0)
    api_base44("PUT", ENDPOINTS["controle"], {
        "lucro_hoje_porcentagem": lucro_atual + lucro_operacao_pct
    }, id_registro=usuario['id'])

def iniciar_loop():
    print("🚀 MIRAQUANTIA ONLINE - GESTÃO DE METAS ATIVA")
    while True:
        try:
            configs = api_base44("GET", ENDPOINTS["controle"])
            if not configs: time.sleep(60); continue

            ex = ccxt.bybit()
            preco = ex.fetch_ticker(SYMBOL)['last']
            
            for user in configs:
                uid = user.get("usuario_id")
                if not user.get("status_bot"): continue
                
                # Verificação de Metas Diárias
                meta = float(user.get("meta_diaria_porcentagem") or 2.0)
                stop = float(user.get("risco_maximo_porcentagem") or 1.0)
                lucro_hoje = float(user.get("lucro_hoje_porcentagem") or 0.0)

                if lucro_hoje >= meta:
                    print(f"✅ {uid} atingiu a meta do dia ({lucro_hoje:.2f}%). Aguardando amanhã.")
                    continue
                if lucro_hoje <= -stop:
                    print(f"🛑 {uid} atingiu o limite de risco ({lucro_hoje:.2f}%). Robô pausado por segurança.")
                    continue

                # Verificação de Ordem Aberta[cite: 3]
                todas_ops = api_base44("GET", ENDPOINTS["operacao"])
                tem_aberta = any(o for o in todas_ops if o['usuario_id'] == uid and o['status'] == 'Aberta' and o['categoria_ordem'] != 'Fantasma')
                
                if not tem_aberta:
                    rsi_alvo = user.get("rsi_alvo_compra", 40)
                    # Simulação de gatilho para manter o fluxo constante até a meta
                    if preco < 100000: 
                        print(f"💰 Operando para atingir meta diária de {meta}% em {uid}")
                        res = api_base44("POST", ENDPOINTS["operacao"], {
                            "usuario_id": uid, "par_moeda": SYMBOL, "tipo_ordem": "Compra",
                            "categoria_ordem": "Demo", "preco_entrada": preco,
                            "data_hora": datetime.now().isoformat(), "status": "Aberta"
                        })
                        if res: operacoes_memoria[uid] = {"id": res['id'], "entrada": preco}

                elif uid in operacoes_memoria:
                    op = operacoes_memoria[uid]
                    lucro_pct = ((preco - op["entrada"]) / op["entrada"]) * 100
                    
                    # Alvos curtos para atingir a meta em várias operações[cite: 3]
                    if lucro_pct >= 0.5 or lucro_pct <= -0.5:
                        api_base44("PUT", ENDPOINTS["operacao"], {
                            "preco_saida": preco, "lucro_porcentagem": lucro_pct,
                            "status": "Fechada"
                        }, id_registro=op["id"])
                        atualizar_progresso_diario(user, lucro_pct)
                        del operacoes_memoria[uid]

            time.sleep(60)
        except Exception as e:
            print(f"Erro: {e}"); time.sleep(60)

if __name__ == "__main__":
    iniciar_loop()
