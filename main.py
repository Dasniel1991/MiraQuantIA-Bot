import ccxt
import time
import requests
import os
import json
from datetime import datetime
from google import genai

# Configurações de Ambiente
BASE44_API_KEY = os.environ.get("BASE44_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

cliente_ia = genai.Client(api_key=GEMINI_API_KEY)
MODELO_GEMINI = "gemini-3-flash-preview"

BASE_URL = "https://miraquant-ia.base44.app/api"
ENDPOINTS = {
    "controle": f"{BASE_URL}/entities/ControleBot",
    "memoria": f"{BASE_URL}/entities/Memoria_IA",
    "historico": f"{BASE_URL}/entities/HistoricoOperacoes"
}

SYMBOL = 'BTC/USDT'
TIMEFRAME = '1m' # Timeframe em 1m para testes rápidos
usuarios_em_descanso = {}

def api_base44(metodo, endpoint, dados=None):
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    try:
        if metodo == "GET":
            res = requests.get(endpoint, headers=headers)
        else:
            res = requests.post(endpoint, json=dados, headers=headers)
        return res.json() if res.status_code in [200, 201] else []
    except:
        return []

def gravar_memoria_ia(usuario, contexto, decisao, justificativa):
    payload = {
        "usuario_id": usuario,
        "data_hora": datetime.now().isoformat(),
        "contexto_mercado": contexto,
        "decisao_ia": decisao,
        "justificativa": justificativa
    }
    api_base44("POST", ENDPOINTS["memoria"], payload)

def consultar_ia(preco, rsi, meta):
    prompt = f"BTC a {preco:.2f}, RSI {rsi:.2f}. Meta {meta}%. Decida: COMPRAR ou IGNORAR? Responda apenas JSON: {{\"decisao\": \"...\", \"justificativa\": \"...\"}}"
    try:
        res = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        # LINHA CORRIGIDA - Agora com todas as aspas e parênteses fechados
        limpo = res.text.replace("```json", "").replace("```", "").strip()
        return json.loads(limpo)
    except:
        return {"decisao": "IGNORAR", "justificativa": "Erro na IA"}

def iniciar_loop():
    print("🚀 MIRAQUANTIA ONLINE - MONITORAMENTO ATIVO 24/7")
    while True:
        configs = api_base44("GET", ENDPOINTS["controle"])
        for user in configs:
            uid = user.get("usuario_id")
            if not user.get("status_bot"): continue
            
            # Verificação de Descanso
            if uid in usuarios_em_descanso and time.time() < usuarios_em_descanso[uid]:
                continue

            # Conexão e Dados
            ex = ccxt.bybit({'options': {'defaultType': 'spot'}})
            try:
                preco = ex.fetch_ticker(SYMBOL)['last']
            except Exception as e:
                print(f"Erro ao buscar preço: {e}")
                continue
                
            rsi = 50 # Simplificado para o teste
            
            # IA Decide
            analise = consultar_ia(preco, rsi, user.get('meta_diaria_porcentagem', 2))
            
            # LOG DE PENSAMENTO (Para você ver no Coolify)
            print(f"[{datetime.now().strftime('%H:%M')}] Usuário: {uid} | IA diz: {analise['decisao']} | Motivo: {analise['justificativa']}")
            
            # Sempre grava na memória para você ver na Base44
            gravar_memoria_ia(uid, f"Preço: {preco}", analise['decisao'], analise['justificativa'])

            if analise['decisao'] == "COMPRAR":
                print(f"⚠️ {uid} executou ordem de COMPRA!")
                usuarios_em_descanso[uid] = time.time() + 300 # Descanso de 5 min pós-operação

        time.sleep(60)

if __name__ == "__main__":
    iniciar_loop()
