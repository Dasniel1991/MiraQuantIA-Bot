import ccxt
import time
import requests
import os
import json
from datetime import datetime
from google import genai

# ==========================================
# 1. CONFIGURAÇÕES DE AMBIENTE
# ==========================================
BASE44_API_KEY = os.environ.get("BASE44_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

cliente_ia = genai.Client(api_key=GEMINI_API_KEY)
MODELO_GEMINI = "gemini-3-flash-preview"

# Endpoints da Base44
BASE_URL = "https://miraquant-ia.base44.app/api"
ENDPOINTS = {
    "controle": f"{BASE_URL}/entities/ControleBot",
    "memoria": f"{BASE_URL}/entities/Memoria_IA",
    "webhook": f"{BASE_URL}/functions/webhookRobo",
    "saldo": f"{BASE_URL}/entities/SaldoUsuario",
    "historico": f"{BASE_URL}/entities/HistoricoOperacoes"
}

SYMBOL = 'BTC/USDT'
TIMEFRAME = '15m'
QUANTIDADE_USDT = 50.0 
usuarios_em_descanso = {} 

# ==========================================
# 2. FUNÇÕES DE COMUNICAÇÃO (PONTE)
# ==========================================

def api_base44(metodo, endpoint, dados=None):
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    try:
        if metodo == "GET":
            res = requests.get(endpoint, headers=headers)
        else:
            res = requests.post(endpoint, json=dados, headers=headers)
        return res.json() if res.status_code in [200, 201] else None
    except Exception as e:
        print(f"Erro API Base44: {e}")
        return None

def registrar_operacao_no_painel(config, tipo_conta, preco, acao, status="Aberta"):
    """Envia os dados exatos para o gráfico e histórico do painel"""
    payload = {
        "usuario_id": config.get("usuario_id"),
        "par_moeda": SYMBOL,
        "tipo_conta": tipo_conta, # 'demo' ou 'real'
        "tipo_ordem": acao,       # 'Compra' ou 'Venda'
        "preco_entrada": preco,
        "data_hora": datetime.now().isoformat(),
        "status": status,
        "timestamp_grafico": int(time.time() * 1000) # Necessário para o TradingView/Gráfico
    }
    api_base44("POST", ENDPOINTS["historico"], payload)

# ==========================================
# 3. LÓGICA DE TRADING
# ==========================================

def consultar_ia(preco, rsi, meta):
    prompt = f"""
    Analise Técnica: {SYMBOL} a {preco:.2f} USDT com RSI de {rsi:.2f}.
    Meta Diária do Usuário: {meta}%.
    Decida se é o momento de COMPRAR para buscar lucro rápido.
    Responda apenas JSON: {{"decisao": "COMPRAR" ou "IGNORAR", "justificativa": "..."}}
    """
    try:
        res = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        limpo = res.text.replace("```json", "").replace("
```", "").strip()
        return json.loads(limpo)
    except:
        return {"decisao": "IGNORAR", "justificativa": "Erro na IA"}

def executar_ciclo(config):
    u_id = config.get("usuario_id")
    modo = str(config.get("modo_operacao", "demo")).lower()
    
    # Setup da Exchange
    exchange = ccxt.bybit({
        'apiKey': config.get('chave_api'), 'secret': config.get('secret_api'),
        'enableRateLimit': True, 'options': {'defaultType': 'spot'}
    })

    # Dados de Mercado
    velas = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=30)
    preco_atual = velas[-1][4]
    
    # (Cálculo simplificado de RSI para o exemplo)
    rsi_fake = 50 

    analise = consultar_ia(preco_atual, rsi_fake, config.get('meta_diaria_porcentagem', 2))
    
    if analise['decisao'] == "COMPRAR":
        print(f"🎯 {u_id} | Decisão: COMPRAR ({modo.upper()})")
        
        if modo == "real":
            # Aqui entraria a ordem real via CCXT
            registrar_operacao_no_painel(config, "real", preco_atual, "Compra")
        else:
            # Simulação Demo
            registrar_operacao_no_painel(config, "demo", preco_atual, "Compra")
        
        usuarios_em_descanso[u_id] = time.time() + 7200
        return True
    return False

def iniciar_robo():
    print("=== MIRAQUANTIA V2: RODANDO E INTEGRADO AO GRÁFICO ===")
    while True:
        configs = api_base44("GET", ENDPOINTS["controle"])
        if configs:
            for user in configs:
                uid = user.get("usuario_id")
                
                # Pula se estiver em descanso ou desligado
                if not user.get("status_bot") or (uid in usuarios_em_descanso and time.time() < usuarios_em_descanso[uid]):
                    continue
                
                executar_ciclo(user)
        
        time.sleep(60)

if __name__ == "__main__":
    iniciar_robo()
