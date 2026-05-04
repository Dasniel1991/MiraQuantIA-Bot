import ccxt
import time
import requests
import os
import json
from datetime import datetime
from google import genai

# ==========================================
# 1. CHAVES DO SERVIDOR (COOLIFY)
# Estas são as únicas chaves que ficam no servidor (para o bot existir)
# ==========================================
BASE44_API_KEY = os.environ.get("BASE44_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ==========================================
# 2. CONFIGURAÇÃO DA IA E DA BASE44
# ==========================================
cliente_ia = genai.Client(api_key=GEMINI_API_KEY)
MODELO_GEMINI = "gemini-3-flash-preview"

BASE44_WEBHOOK_URL = "https://miraquant-ia.base44.app/api/functions/webhookRobo"
BASE44_MEMORIA_URL = "https://miraquant-ia.base44.app/api/entities/Memoria_IA"
BASE44_CONTROLE_URL = "https://miraquant-ia.base44.app/api/entities/ControleBot"

# Parâmetros Fixos
SYMBOL = 'BTC/USDT'
TIMEFRAME = '15m'
QUANTIDADE_COMPRA_USDT = 50.0 

# Dicionário para controlar o descanso de cada usuário separadamente
usuarios_em_descanso = {} 

def obter_configuracoes_painel():
    """Busca a lista de TODOS os usuários cadastrados na Base44"""
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    try:
        response = requests.get(BASE44_CONTROLE_URL, headers=headers)
        if response.status_code in [200, 201]:
            return response.json() # Retorna todos os clientes
        return []
    except Exception as e:
        print(f"[Aviso] Erro de conexão com a Base44: {e}")
        return []

def calcular_rsi(fechamentos, periodo=14):
    if len(fechamentos) < periodo + 1: return 50
    deltas = [fechamentos[i+1] - fechamentos[i] for i in range(len(fechamentos)-1)]
    ganhos = [d if d > 0 else 0 for d in deltas]
    perdas = [-d if d < 0 else 0 for d in deltas]
    media_ganhos = sum(ganhos[-periodo:]) / periodo
    media_perdas = sum(perdas[-periodo:]) / periodo
    if media_perdas == 0: return 100
    rs = media_ganhos / media_perdas
    return 100 - (100 / (1 + rs))

def gravar_memoria_ia(contexto, decisao, justificativa):
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    payload_memoria = {
        "data_hora": datetime.now().isoformat(),
        "par_moeda": SYMBOL,
        "contexto_mercado": contexto,
        "decisao_ia": decisao,
        "justificativa": justificativa,
        "resultado_lucro_porcentagem": 0.00 
    }
    try:
        requests.post(BASE44_MEMORIA_URL, json=payload_memoria, headers=headers)
    except:
        pass

def enviar_lucro_base44(payload_dados):
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    try:
        requests.post(BASE44_WEBHOOK_URL, json=payload_dados, headers=headers)
    except Exception as e:
        print(f"[Base44 - ERRO] Erro ao atualizar painel: {e}")

def consultar_cerebro_gemini(preco, rsi, meta_diaria_porcentagem):
    contexto = f"O ativo {SYMBOL} está custando {preco:.2f} USDT. O RSI atual no tempo gráfico de {TIMEFRAME} é de {rsi:.2f}."
    
    prompt = f"""
    Você é o MiraQuantIA, um robô institucional de trading quantitativo focado em bater uma META DIÁRIA de {meta_diaria_porcentagem}%.
    
    Cenário atual: {contexto}
    
    Regra: 
    - Um RSI abaixo de 55 indica correção.
    - Acima de 65 indica perigo.
    
    Devemos COMPRAR agora? Responda EXATAMENTE em JSON:
    {{"decisao": "COMPRAR", "justificativa": "Motivo aqui"}} ou {{"decisao": "IGNORAR", "justificativa": "Motivo aqui"}}
    """
    try:
        resposta = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        texto_resposta = resposta.text.replace("```json", "").replace("```", "").strip()
        analise = json.loads(texto_resposta)
        return analise['decisao'], analise['justificativa'], contexto
    except Exception as e:
        return "IGNORAR", f"Erro da IA: {e}", contexto

def executar_ordem_real(exchange, preco_atual, meta_diaria, risco_maximo, config):
    usuario_id = config.get("usuario_id", "admin")
    try:
        print(f"🔄 Executando Ordem REAL na Bybit para {usuario_id}...")
        ordem_compra = exchange.create_market_buy_order(SYMBOL, QUANTIDADE_COMPRA_USDT)
        preco_entrada = ordem_compra['price'] if ordem_compra['price'] else preco_atual
        
        preco_alvo = preco_entrada * (1 + meta_diaria)
        preco_stop = preco_entrada * (1 - risco_maximo)
        
        try:
            exchange.create_order(SYMBOL, 'limit', 'sell', QUANTIDADE_COMPRA_USDT, preco_alvo, {'reduceOnly': True})
            exchange.create_order(SYMBOL, 'stop_market', 'sell', QUANTIDADE_COMPRA_USDT, None, {'stopPrice': preco_stop, 'reduceOnly': True})
        except Exception as protect_e:
            print(f"⚠️ Aviso de proteção real: {protect_e}")

        enviar_lucro_base44({
            "usuario_id": usuario_id, "par_moeda": SYMBOL, "tipo_ordem": "Compra",
            "preco_entrada": preco_entrada, "preco_saida": 0, "lucro_porcentagem": 0, "lucro_financeiro": 0, "status": "Aberta"
        })
        
        # Coloca apenas este usuário específico em descanso de 2 horas (7200 segundos)
        usuarios_em_descanso[usuario_id] = time.time() + 7200

    except Exception as e:
        print(f"❌ ERRO REAL ({usuario_id}): {e}")

def executar_simulacao(exchange, preco_atual, meta_diaria, risco_maximo, config):
    usuario_id = config.get("usuario_id", "admin")
    print(f"\n🚀 SIMULAÇÃO (DEMO) acionada para o usuário {usuario_id}!")
    
    preco_entrada = preco_atual
    preco_alvo = preco_entrada * (1 + meta_diaria)
    
    enviar_lucro_base44({
        "usuario_id": usuario_id, "par_moeda": SYMBOL, "tipo_ordem": "Compra",
        "preco_entrada": preco_entrada, "preco_saida": preco_alvo, "lucro_porcentagem": meta_diaria * 100,
        "lucro_financeiro": 15.00, "status": "Fechada"
    })
    
    # Coloca apenas este usuário específico em descanso de 2 horas (7200 segundos)
    usuarios_em_descanso[usuario_id] = time.time() + 7200

def iniciar_robo():
    print("=====================================================")
    print("=== MIRAQUANTIA AUTÔNOMO (SISTEMA MULTI-USUÁRIO)  ===")
    print("=====================================================")
    
    while True:
        try:
            configs = obter_configuracoes_painel()
            if not configs:
                time.sleep(60)
                continue
                
            # O robô agora analisa todos os clientes cadastrados na Base44
            for config in configs:
                usuario_id = config.get('usuario_id', 'Desconhecido')
                status_bot = config.get('status_bot', False)
                
                # 1. Verifica se o robô deste cliente está ligado
                if status_bot == False or str(status_bot).lower() == 'false':
                    continue

                # 2. Verifica se o cliente está no período de descanso pós-operação
                if usuario_id in usuarios_em_descanso:
                    if time.time() < usuarios_em_descanso[usuario_id]:
                        continue # Ainda está descansando, pula para o próximo cliente
                    else:
                        del usuarios_em_descanso[usuario_id] # Terminou o descanso

                chave_bybit = config.get('chave_api')
                secret_bybit = config.get('secret_api')
                modo_operacao = str(config.get('modo_operacao', 'demo')).lower() 
                
                meta_diaria = float(config.get('meta_diaria_porcentagem', 2)) / 100
                risco_maximo = float(config.get('risco_maximo_porcentagem', 5)) / 100

                # 3. Verifica se as chaves da Bybit existem no banco de dados para este cliente
                if not chave_bybit or not secret_bybit:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Chaves Bybit ausentes para o usuário: {usuario_id}")
                    continue

                # 4. Conecta na corretora especificamente com as chaves deste cliente
                exchange = ccxt.bybit({
                    'apiKey': chave_bybit, 'secret': secret_bybit, 'enableRateLimit': True,
                    'urls': {'api': {'public': 'https://api.bytick.com', 'private': 'https://api.bytick.com'}},
                    'options': {'defaultType': 'spot'}
                })

                velas = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=50)
                fechamentos = [vela[4] for vela in velas]
                preco_atual = fechamentos[-1]
                rsi_atual = calcular_rsi(fechamentos)
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Analisando {usuario_id} | MODO: {modo_operacao.upper()}")
                
                decisao, justificativa, contexto = consultar_cerebro_gemini(preco_atual, rsi_atual, (meta_diaria*100))
                
                if decisao == "COMPRAR":
                    gravar_memoria_ia(contexto, decisao, justificativa)
                    if modo_operacao == 'real':
                        executar_ordem_real(exchange, preco_atual, meta_diaria, risco_maximo, config)
                    else:
                        executar_simulacao(exchange, preco_atual, meta_diaria, risco_maximo, config)

            # Após analisar todos os clientes, descansa 1 minuto e recomeça o ciclo
            time.sleep(60)

        except Exception as e:
            print(f"Erro no ciclo principal: {e}")
            time.sleep(60)

if __name__ == "__main__":
    iniciar_robo()
