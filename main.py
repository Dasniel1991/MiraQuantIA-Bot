import ccxt
import time
import requests
import os
import json
from datetime import datetime
from google import genai

# ==========================================
# 1. PUXANDO APENAS A CHAVE DA BASE44 DO RAILWAY
# ==========================================
BASE44_API_KEY = os.environ.get("BASE44_API_KEY")

# ==========================================
# 2. CONFIGURAÇÃO DA IA E DA BASE44
# ==========================================
cliente_ia = genai.Client()
MODELO_GEMINI = "gemini-3-flash-preview"

# URLs REAIS E OFICIAIS DA SUA APLICAÇÃO BASE44
BASE44_WEBHOOK_URL = "https://miraquant-ia.base44.app/api/functions/webhookRobo"
BASE44_MEMORIA_URL = "https://miraquant-ia.base44.app/api/entities/Memoria_IA"
BASE44_CONTROLE_URL = "https://miraquant-ia.base44.app/api/entities/ControleBot"

SYMBOL = 'BTC/USDT'
TIMEFRAME = '15m'

def obter_configuracoes_painel():
    """Consulta a entidade ControleBot na Base44 para pegar as chaves da Bybit e as Metas."""
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
        print(f"[Aviso] Erro de conexão com a Base44 ao buscar configurações: {e}")
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
        print(f"[Base44 - ERRO] Problema ao gravar memória: {e}")

def enviar_lucro_base44(payload_dados):
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    try:
        response = requests.post(BASE44_WEBHOOK_URL, json=payload_dados, headers=headers)
        if response.status_code in [200, 201]:
            print("[Base44] Dashboard atualizado com o lucro diário com sucesso!")
    except Exception as e:
        print(f"[Base44 - ERRO] Erro de comunicação com o Dashboard: {e}")

def consultar_cerebro_gemini(preco, rsi, meta_diaria_porcentagem):
    contexto = f"O ativo {SYMBOL} está custando {preco:.2f} USDT. O RSI atual no tempo gráfico de {TIMEFRAME} é de {rsi:.2f}."
    
    prompt = f"""
    Você é o MiraQuantIA, um robô institucional de trading quantitativo focado em bater uma META DIÁRIA de {meta_diaria_porcentagem}%.
    Você é conservador, mas TEM a obrigação de encontrar janelas de oportunidade seguras todos os dias.
    
    Cenário atual do mercado:
    {contexto}
    
    Nova Regra de Operação Diária: 
    - Um RSI abaixo de 55 já indica que o ativo corrigiu o suficiente para buscar um ganho rápido.
    - Se o RSI estiver acima de 65, o mercado está esticado, então você deve IGNORAR.
    
    Com base nesses dados para garantir o lucro, devemos COMPRAR agora? 
    Responda EXATAMENTE neste formato JSON, sem adicionar mais nenhum texto ou formatação markdown:
    {{"decisao": "COMPRAR", "justificativa": "Sua explicação curta aqui"}}
    ou
    {{"decisao": "IGNORAR", "justificativa": "Sua explicação curta aqui"}}
    """
    
    try:
        resposta = cliente_ia.models.generate_content(
            model=MODELO_GEMINI,
            contents=prompt
        )
        texto_resposta = resposta.text.replace("```json", "").replace("```", "").strip()
        analise = json.loads(texto_resposta)
        return analise['decisao'], analise['justificativa'], contexto
    except Exception as e:
        return "IGNORAR", f"Erro no processamento da IA: {e}", contexto

def iniciar_robo():
    print("==================================================")
    print("=== MIRAQUANTIA AUTÔNOMO LIGADO (CONTA DEMO) ===")
    print("==================================================")
    
    while True:
        try:
            # 0. LER CONFIGURAÇÕES DA SUA TELA NA BASE44
            config = obter_configuracoes_painel()
            
            if not config:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Aguardando você preencher as configurações no painel da Base44...")
                time.sleep(60)
                continue
                
            # --- LINHA DE RAIO-X ADICIONADA AQUI ---
            print(f"\n[RAIO-X BASE44] Dados recebidos do banco: {config}")
            # ---------------------------------------

            status_bot = config.get('status_bot', False)
            if status_bot == False or str(status_bot).lower() == 'false':
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🛑 KILL SWITCH ATIVADO. Robô desligado no painel. Aguardando...")
                time.sleep(60)
                continue

            chave_bybit = config.get('chave_api')
            secret_bybit = config.get('secret_api')
            
            meta_diaria = float(config.get('meta_diaria_porcentagem', 2)) / 100
            risco_maximo = float(config.get('risco_maximo_porcentagem', 5)) / 100

            if not chave_bybit or not secret_bybit:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Chaves da Bybit ausentes. Preencha e salve na tela de Configurações!")
                time.sleep(60)
                continue

            # 1. CONECTAR NA BYBIT DINAMICAMENTE
            exchange = ccxt.bybit({
                'apiKey': chave_bybit,
                'secret': secret_bybit,
                'enableRateLimit': True,
                'urls': {'api': {'public': 'https://api.bytick.com', 'private': 'https://api.bytick.com'}},
                'options': {'defaultType': 'spot'}
            })

            # 2. Olhos: Coleta dados
            velas = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=50)
            fechamentos = [vela[4] for vela in velas]
            preco_atual = fechamentos[-1]
            rsi_atual = calcular_rsi(fechamentos)
            
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Mercado: Preço {preco_atual:.2f} | RSI {rsi_atual:.2f}")
            print(f"Meta configurada no painel: {meta_diaria*100}% | Risco: {risco_maximo*100}%")
            
            # 3. Cérebro: Pede decisão para a IA
            decisao, justificativa, contexto = consultar_cerebro_gemini(preco_atual, rsi_atual, (meta_diaria*100))
            
            print(f"Decisão: {decisao}")
            gravar_memoria_ia(contexto, decisao, justificativa)
            
            # 4. Ação Simulatória (Paper Trading)
            if decisao == "COMPRAR":
                print("\n!!! GATILHO ACIONADO PELA IA !!! Iniciando Simulação do Mercado...")
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
                            print("\n✅ VITÓRIA! O mercado atingiu o alvo configurado no painel.")
                            payload_operacao = {
                                "usuario_id": config.get("usuario_id", "admin@miraquantia.com"), 
                                "par_moeda": SYMBOL,
                                "tipo_ordem": "Compra",
                                "preco_entrada": preco_entrada,
                                "preco_saida": preco_agora,
                                "lucro_porcentagem": meta_diaria * 100,
                                "lucro_financeiro": 15.00, 
                                "status": "Fechada"
                            }
                            enviar_lucro_base44(payload_operacao)
                            operacao_aberta = False
                            time.sleep(7200)

                        elif preco_agora <= preco_stop:
                            print(f"\n❌ STOP LOSS! O mercado caiu os {risco_maximo*100}% configurados.")
                            operacao_aberta = False
                            time.sleep(3600)

                    except Exception as e:
                        time.sleep(10)
            else:
                time.sleep(900) 

        except Exception as e:
            print(f"Erro no ciclo principal: {e}")
            time.sleep(60)

if __name__ == "__main__":
    iniciar_robo()
