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
    "historico": f"{BASE_URL}/entities/HistoricoOperacoes"
}

SYMBOL = 'BTC/USDT'
TIMEFRAME = '1m'
ARQUIVO_ESTRATEGIA = "estrategia_ia.json"

# Memória RAM do Robô (Zera se reiniciar, o que é bom para curto prazo)
ordens_fantasma = []
historico_hora = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
ultima_reuniao_ia = 0 # Guarda o horário da última vez que a IA rodou

# ==========================================
# 2. FUNÇÕES DE APOIO
# ==========================================
def api_base44(metodo, endpoint, dados=None):
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    try:
        if metodo == "GET":
            res = requests.get(endpoint, headers=headers)
        else:
            res = requests.post(endpoint, json=dados, headers=headers)
        if res.status_code not in [200, 201]: return []
        return res.json()
    except:
        return []

def carregar_estrategia():
    """Lê as regras atuais ditadas pela IA. Se não existir, cria o padrão."""
    if not os.path.exists(ARQUIVO_ESTRATEGIA):
        padrao = {"rsi_alvo_compra": 35, "observacao_ia": "Início padrão. Aguardando primeira análise."}
        with open(ARQUIVO_ESTRATEGIA, "w") as f:
            json.dump(padrao, f)
        return padrao
    with open(ARQUIVO_ESTRATEGIA, "r") as f:
        return json.load(f)

def salvar_estrategia(nova_estrategia):
    with open(ARQUIVO_ESTRATEGIA, "w") as f:
        json.dump(nova_estrategia, f)

# ==========================================
# 3. O CÉREBRO: A IA GESTORA (RODA A CADA 25 MINUTOS)
# ==========================================
def reuniao_com_ia_gestora(preco_atual, rsi_atual):
    print("\n" + "="*50)
    print("🧠 [IA GESTORA] Iniciando análise de mercado dos últimos 25 minutos...")
    
    estrategia_atual = carregar_estrategia()
    
    prompt = f"""
    Você é o Gestor Quantitativo do robô MiraQuantIA.
    Estamos operando {SYMBOL}. Preço atual: {preco_atual:.2f}. RSI atual: {rsi_atual:.2f}.
    
    RESULTADOS DOS ÚLTIMOS 25 MINUTOS:
    - O robô operário estava configurado para comprar apenas se o RSI caísse para {estrategia_atual['rsi_alvo_compra']}.
    - Operações Reais que bateram a meta: {historico_hora['reais_vitorias']}
    - Operações Reais que deram prejuízo: {historico_hora['reais_derrotas']}
    
    OPORTUNIDADES IGNORADAS (Shadow Trading):
    Nós simulamos entradas toda vez que o RSI chegou perto da nossa meta (entre {estrategia_atual['rsi_alvo_compra']} e 50).
    - Entradas ignoradas que TERIAM DADO LUCRO: {historico_hora['fantasma_vitorias']}
    - Entradas ignoradas que TERIAM DADO PREJUÍZO: {historico_hora['fantasma_derrotas']}
    
    TAREFA:
    Se estamos ignorando muitas vitórias, aumente o 'rsi_alvo_compra' para o robô entrar mais. 
    Se o mercado está perigoso, diminua o 'rsi_alvo_compra' para ele ser mais conservador.
    
    Responda APENAS um JSON válido neste formato exato:
    {{"rsi_alvo_compra": 40, "observacao_ia": "Motivo da sua decisão baseado nos dados acima."}}
    """
    
    try:
        res = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        limpo = res.text.replace("```json", "").replace("```", "").strip()
        nova_regra = json.loads(limpo)
        
        salvar_estrategia(nova_regra)
        print(f"✅ [IA GESTORA] Nova ordem emitida! O robô agora comprará no RSI: {nova_regra['rsi_alvo_compra']}")
        print(f"📝 Justificativa da IA: {nova_regra['observacao_ia']}")
        print("="*50 + "\n")
        
        # Zera o placar para o próximo bloco de 25 minutos
        global historico_hora
        historico_hora = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
        
    except Exception as e:
        print(f"❌ [ERRO IA GESTORA] Falha ao gerar nova regra: {e}. Mantendo regra anterior.")

# ==========================================
# 4. O OPERÁRIO: LOOP PRINCIPAL (A CADA 1 MIN)
# ==========================================
def iniciar_loop():
    global ultima_reuniao_ia, ordens_fantasma, historico_hora
    print("🚀 MIRAQUANTIA V3 - ARQUITETURA MESTRE-OPERÁRIO INICIADA")
    
    while True:
        try:
            # 1. Verifica se está na hora da reunião com a IA (1500 segundos = 25 minutos)
            if time.time() - ultima_reuniao_ia > 1500:
                ex = ccxt.bybit({'options': {'defaultType': 'spot'}})
                preco_atual = ex.fetch_ticker(SYMBOL)['last']
                rsi_simulado = 45 # Usando um RSI fixo para não poluir o código com cálculo matemático longo agora
                reuniao_com_ia_gestora(preco_atual, rsi_simulado)
                ultima_reuniao_ia = time.time()

            estrategia = carregar_estrategia()
            limite_rsi = estrategia['rsi_alvo_compra']
            
            ex = ccxt.bybit({'options': {'defaultType': 'spot'}})
            preco = ex.fetch_ticker(SYMBOL)['last']
            rsi = 45 # Apenas exemplo de RSI atual do mercado. 
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Operário: Preço={preco} | RSI={rsi} | Ordem do Chefe: Comprar no RSI {limite_rsi}")

            # 2. Rastreamento das Ordens Fantasmas (Acompanha o que ignoramos)
            for ordem in ordens_fantasma[:]: # Copia da lista para não bugar ao remover itens
                if preco >= ordem['preco_alvo']:
                    historico_hora['fantasma_vitorias'] += 1
                    ordens_fantasma.remove(ordem)
                    print(f"👻 [FANTASMA] Uma oportunidade ignorada teria dado LUCRO!")
                elif preco <= ordem['preco_stop']:
                    historico_hora['fantasma_derrotas'] += 1
                    ordens_fantasma.remove(ordem)
                    print(f"👻 [FANTASMA] Uma oportunidade ignorada teria dado PREJUÍZO. (Ainda bem que não entramos)")

            # 3. Decisão do Operário Baseada na Regra da IA
            if rsi <= limite_rsi:
                print(f"⚠️ [MERCADO] RSI atingiu a meta ({limite_rsi})! Robô executando COMPRA!")
                # Aqui entra o código de compra na Base44 que já temos
                # historico_hora['reais_vitorias'] += 1 (Adicionar isso na lógica de fechamento de lucro futuramente)
                time.sleep(60) # Descansa um pouco após comprar
            
            # 4. Criação de Ordem Fantasma (Se chegou perto, mas a regra não deixou comprar)
            elif rsi <= (limite_rsi + 15): # Se a regra é 35, e o RSI bater 50, ele anota
                ordens_fantasma.append({
                    "preco_entrada": preco,
                    "preco_alvo": preco * 1.02, # Alvo de 2%
                    "preco_stop": preco * 0.95  # Stop de 5%
                })
                print(f"👁️ [OLHEIRO] RSI em {rsi}. Não comprei, mas registrei como Fantasma para a IA avaliar depois.")

            time.sleep(60) # Repete a cada 1 minuto

        except Exception as e:
            print(f"Erro no ciclo do operário: {e}")
            time.sleep(60)

if __name__ == "__main__":
    # Força a primeira reunião com a IA assim que ligar o robô
    ultima_reuniao_ia = 0 
    iniciar_loop()
