import ccxt
import time
import requests
import os
import json
from datetime import datetime
import google.generativeai as genai

# ==========================================
# 1. PUXANDO AS VARIÁVEIS SEGURAS DO RAILWAY
# ==========================================
BASE44_APP_ID = os.environ.get("BASE44_APP_ID")
BASE44_API_KEY = os.environ.get("BASE44_API_KEY")
BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# ==========================================
# 2. CONFIGURAÇÃO DA IA E DA BASE44
# ==========================================
# Configura o cérebro do Gemini
genai.configure(api_key=GEMINI_API_KEY)
# Usamos o modelo flash por ser extremamente rápido para tomada de decisões financeiras
modelo_ia = genai.GenerativeModel('gemini-1.5-flash')

# URLs dinâmicas da Base44
BASE44_WEBHOOK_URL = f"https://api.base44.com/v1/apps/{BASE44_APP_ID}/functions/webhookRobo"
BASE44_MEMORIA_URL = f"https://api.base44.com/v1/apps/{BASE44_APP_ID}/entities/Memoria_IA"

# ==========================================
# 3. PARÂMETROS DA ESTRATÉGIA MIRAQUANTIA
# ==========================================
SYMBOL = 'BTC/USDT'
TIMEFRAME = '15m'
META_DIARIA = 0.02

try:
    exchange = ccxt.bybit({
        'apiKey': BYBIT_API_KEY,
        'secret': BYBIT_API_SECRET,
        'enableRateLimit': True,
        'options': {'defaultType': 'spot'}
    })
except Exception as e:
    print(f"Erro ao conectar com a Bybit: {e}")

def calcular_rsi(fechamentos, periodo=14):
    """Calcula a Força Relativa do Mercado (RSI)."""
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
    """Salva o pensamento do Gemini na tabela Memoria_IA da Base44."""
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    payload_memoria = {
        "data_hora": datetime.now().isoformat(),
        "par_moeda": SYMBOL,
        "contexto_mercado": contexto,
        "decisao_ia": decisao,
        "justificativa": justificativa,
        "resultado_lucro_porcentagem": 0.00 # Fica zerado até fechar a operação
    }
    try:
        requests.post(BASE44_MEMORIA_URL, json=payload_memoria, headers=headers)
        print("[Base44] Pensamento da IA gravado na Memória (Hipocampo).")
    except Exception as e:
        print(f"[Base44] Erro ao gravar memória: {e}")

def enviar_lucro_base44(payload_dados):
    """Envia o resultado da operação para o Dashboard principal."""
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    try:
        requests.post(BASE44_WEBHOOK_URL, json=payload_dados, headers=headers)
        print("[Base44] Dashboard atualizado com sucesso!")
    except Exception as e:
        print(f"[Base44] Erro de comunicação com o Dashboard: {e}")

def consultar_cerebro_gemini(preco, rsi):
    """Envia os dados do mercado para o Gemini e pede a decisão."""
    contexto = f"O ativo {SYMBOL} está custando {preco:.2f} USDT. O RSI atual no tempo gráfico de {TIMEFRAME} é de {rsi:.2f}."
    
    prompt = f"""
    Você é o MiraQuantIA, um robô institucional de trading quantitativo ultraconservador.
    Sua meta é conseguir operações cirúrgicas de 2% de lucro. Você só entra se a probabilidade for maior que 90%.
    
    Cenário atual do mercado:
    {contexto}
    
    Regra: Um RSI abaixo de 30 indica sobrevenda extrema (boa chance de subida). RSI acima de 70 é risco de queda.
    
    Com base nesses dados rigorosos, devemos COMPRAR agora? 
    Responda EXATAMENTE neste formato JSON, sem adicionar mais nenhum texto ou formatação markdown:
    {{"decisao": "COMPRAR", "justificativa": "Sua explicação curta aqui"}}
    ou
    {{"decisao": "IGNORAR", "justificativa": "Sua explicação curta aqui"}}
    """
    
    try:
        resposta = modelo_ia.generate_content(prompt)
        texto_resposta = resposta.text.replace('```json', '').replace('```', '').strip()
        analise = json.loads(texto_resposta)
        return analise['decisao'], analise['justificativa'], contexto
    except Exception as e:
        print(f"[Gemini] Erro ao consultar a IA: {e}")
        return "IGNORAR", f"Erro no processamento da IA: {e}", contexto

def iniciar_robo():
    print("==================================================")
    print("=== MIRAQUANTIA AUTÔNOMO LIGADO (GEMINI CORE) ===")
    print("==================================================")
    
    while True:
        try:
            # 1. Olhos: Coleta dados
            velas = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=50)
            fechamentos = [vela[4] for vela in velas]
            preco_atual = fechamentos[-1]
            rsi_atual = calcular_rsi(fechamentos)
            
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Mercado: Preço {preco_atual:.2f} | RSI {rsi_atual:.2f}")
            print("Enviando dados para o cérebro Gemini analisar...")
            
            # 2. Cérebro: Pede decisão para a IA
            decisao, justificativa, contexto = consultar_cerebro_gemini(preco_atual, rsi_atual)
            
            print(f"Decisão da IA: {decisao}")
            print(f"Justificativa: {justificativa}")
            
            # 3. Memória: Salva tudo na Base44 para aprendizado futuro
            gravar_memoria_ia(contexto, decisao, justificativa)
            
            # 4. Ação: Puxa o gatilho se a IA mandar
            if decisao == "COMPRAR":
                print("!!! GATILHO ACIONADO PELA IA !!! Operando...")
                
                # Simulação para Dashboard (Até testarmos com dinheiro real)
                preco_saida = preco_atual * (1 + META_DIARIA)
                lucro_usdt = 15.00 
                
                payload_operacao = {
                    "usuario_id": "admin@miraquantia.com", 
                    "par_moeda": SYMBOL,
                    "tipo_ordem": "Compra",
                    "preco_entrada": preco_atual,
                    "preco_saida": preco_saida,
                    "lucro_porcentagem": META_DIARIA * 100,
                    "lucro_financeiro": lucro_usdt,
                    "status": "Fechada"
                }
                
                enviar_lucro_base44(payload_operacao)
                
                print("Dormindo por 1 hora após a operação cirúrgica...")
                time.sleep(3600)
            else:
                # Se a IA disser IGNORAR, espera 2 minutos antes de incomodá-la de novo
                time.sleep(120)

        except Exception as e:
            print(f"Erro no ciclo principal: {e}")
            time.sleep(60)

if __name__ == "__main__":
    iniciar_robo()
