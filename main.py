import ccxt
import time
import requests
import os
import json
from datetime import datetime
from google import genai

# ==========================================
# 1. PUXANDO APENAS A CHAVE DA BASE44
# ==========================================
BASE44_API_KEY = os.environ.get("BASE44_API_KEY")

# ==========================================
# 2. CONFIGURAÇÃO DA IA E DA BASE44
# ==========================================
cliente_ia = genai.Client()
MODELO_GEMINI = "gemini-3-flash-preview"

BASE44_WEBHOOK_URL = "https://miraquant-ia.base44.app/api/functions/webhookRobo"
BASE44_MEMORIA_URL = "https://miraquant-ia.base44.app/api/entities/Memoria_IA"
BASE44_CONTROLE_URL = "https://miraquant-ia.base44.app/api/entities/ControleBot"

# Parâmetros Fixos
SYMBOL = 'BTC/USDT'
TIMEFRAME = '15m'
QUANTIDADE_COMPRA_USDT = 50.0 # Quantidade em Dólares para usar em cada operação no MODO REAL

def obter_configuracoes_painel():
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    try:
        response = requests.get(BASE44_CONTROLE_URL, headers=headers)
        if response.status_code in [200, 201]:
            dados = response.json()
            if len(dados) > 0:
                return dados[0] 
        print(f"[Aviso] Nenhuma configuração encontrada no painel. Status: {response.status_code}")
        return None
    except Exception as e:
        print(f"[Aviso] Erro de conexão com a Base44: {e}")
        return None

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
    except Exception as e:
        pass

def enviar_lucro_base44(payload_dados):
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    try:
        requests.post(BASE44_WEBHOOK_URL, json=payload_dados, headers=headers)
        print("[Base44] Dashboard atualizado com sucesso!")
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
    """Executa a ordem DE VERDADE na Bybit usando o saldo real do usuário."""
    try:
        print(f"🔄 Executando Ordem REAL na Bybit... Arriscando {QUANTIDADE_COMPRA_USDT} USDT")
        
        # 1. Compra a Mercado
        ordem_compra = exchange.create_market_buy_order(SYMBOL, QUANTIDADE_COMPRA_USDT)
        preco_entrada = ordem_compra['price'] if ordem_compra['price'] else preco_atual
        
        # 2. Calcula Alvos
        preco_alvo = preco_entrada * (1 + meta_diaria)
        preco_stop = preco_entrada * (1 - risco_maximo)
        
        # 3. Cria as ordens de proteção OCO (Take Profit e Stop Loss) na corretora
        try:
            exchange.create_order(SYMBOL, 'limit', 'sell', QUANTIDADE_COMPRA_USDT, preco_alvo, {'reduceOnly': True})
            exchange.create_order(SYMBOL, 'stop_market', 'sell', QUANTIDADE_COMPRA_USDT, None, {'stopPrice': preco_stop, 'reduceOnly': True})
            print(f"✅ Ordens de Proteção Reais criadas! Alvo: {preco_alvo:.2f} | Stop: {preco_stop:.2f}")
        except Exception as protect_e:
            print(f"⚠️ Aviso: Não foi possível criar as ordens de proteção automáticas: {protect_e}")

        # Envia evento de "Aberta" pro Dashboard
        enviar_lucro_base44({
            "usuario_id": config.get("usuario_id", "admin"), "par_moeda": SYMBOL, "tipo_ordem": "Compra",
            "preco_entrada": preco_entrada, "preco_saida": 0, "lucro_porcentagem": 0, "lucro_financeiro": 0, "status": "Aberta"
        })
        
        print("Robô descansando 2 horas após efetuar ordem real no mercado...")
        time.sleep(7200)

    except Exception as e:
        print(f"❌ ERRO GRAVE AO ENVIAR ORDEM REAL: {e}")

def executar_simulacao(exchange, preco_atual, meta_diaria, risco_maximo, config):
    """Faz o Paper Trading (Conta Demo)."""
    print("\n!!! GATILHO ACIONADO !!! Iniciando Simulação (MODO DEMO)...")
    preco_entrada = preco_atual
    preco_alvo = preco_entrada * (1 + meta_diaria)
    preco_stop = preco_entrada * (1 - risco_maximo) 

    print(f"💰 COMPRA SIMULADA: {preco_entrada:.2f} USDT")
    print(f"🎯 ALVO DA META (+{meta_diaria*100}%): {preco_alvo:.2f} USDT")
    print(f"🛡️ STOP LOSS (-{risco_maximo*100}%): {preco_stop:.2f} USDT")

    operacao_aberta = True
    while operacao_aberta:
        time.sleep(60) 
        try:
            ticker = exchange.fetch_ticker(SYMBOL)
            preco_agora = ticker['last']
            
            if preco_agora >= preco_alvo:
                print("\n✅ VITÓRIA DEMO! O mercado atingiu o alvo.")
                enviar_lucro_base44({
                    "usuario_id": config.get("usuario_id", "admin"), "par_moeda": SYMBOL, "tipo_ordem": "Compra",
                    "preco_entrada": preco_entrada, "preco_saida": preco_agora, "lucro_porcentagem": meta_diaria * 100,
                    "lucro_financeiro": 15.00, "status": "Fechada"
                })
                operacao_aberta = False
                time.sleep(7200)

            elif preco_agora <= preco_stop:
                print(f"\n❌ STOP LOSS DEMO! O mercado caiu os {risco_maximo*100}% configurados.")
                operacao_aberta = False
                time.sleep(3600)
        except:
            time.sleep(10)

def iniciar_robo():
    print("==================================================")
    print("=== MIRAQUANTIA AUTÔNOMO (VERIFICAÇÃO DE MODO) ===")
    print("==================================================")
    
    while True:
        try:
            config = obter_configuracoes_painel()
            if not config:
                time.sleep(60)
                continue
                
            status_bot = config.get('status_bot', False)
            if status_bot == False or str(status_bot).lower() == 'false':
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🛑 Robô Desligado no painel. Aguardando...")
                time.sleep(60)
                continue

            chave_bybit = config.get('chave_api')
            secret_bybit = config.get('secret_api')
            modo_operacao = str(config.get('modo_operacao', 'demo')).lower() # <- LÊ O MODO DO PAINEL
            
            meta_diaria = float(config.get('meta_diaria_porcentagem', 2)) / 100
            risco_maximo = float(config.get('risco_maximo_porcentagem', 5)) / 100

            if not chave_bybit or not secret_bybit:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Chaves ausentes.")
                time.sleep(60)
                continue

            exchange = ccxt.bybit({
                'apiKey': chave_bybit, 'secret': secret_bybit, 'enableRateLimit': True,
                'urls': {'api': {'public': 'https://api.bytick.com', 'private': 'https://api.bytick.com'}},
                'options': {'defaultType': 'spot'}
            })

            velas = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=50)
            fechamentos = [vela[4] for vela in velas]
            preco_atual = fechamentos[-1]
            rsi_atual = calcular_rsi(fechamentos)
            
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Mercado: {preco_atual:.2f} | RSI {rsi_atual:.2f} | MODO: {modo_operacao.upper()}")
            
            decisao, justificativa, contexto = consultar_cerebro_gemini(preco_atual, rsi_atual, (meta_diaria*100))
            
            print(f"Decisão: {decisao}")
            gravar_memoria_ia(contexto, decisao, justificativa)
            
            if decisao == "COMPRAR":
                # --- DIRECIONAMENTO DE MODO ---
                if modo_operacao == 'real':
                    executar_ordem_real(exchange, preco_atual, meta_diaria, risco_maximo, config)
                else:
                    executar_simulacao(exchange, preco_atual, meta_diaria, risco_maximo, config)
            else:
                time.sleep(900) 

        except Exception as e:
            print(f"Erro no ciclo principal: {e}")
            time.sleep(60)

if __name__ == "__main__":
    iniciar_robo()
