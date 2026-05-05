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

# Memória RAM do Robô (Agora é separada por usuário!)
ordens_fantasma = {}
historico_hora = {}
ultima_reuniao_ia = {}

# ==========================================
# 2. FUNÇÕES DE APOIO
# ==========================================
def api_base44(metodo, endpoint, dados=None, id_registro=None):
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    url = f"{endpoint}/{id_registro}" if id_registro else endpoint
    try:
        if metodo == "GET":
            res = requests.get(url, headers=headers)
        elif metodo == "POST":
            res = requests.post(url, json=dados, headers=headers)
        elif metodo == "PUT": # Comando para ATUALIZAR a regra da IA no banco
            res = requests.put(url, json=dados, headers=headers)
        
        if res.status_code not in [200, 201, 204]: return []
        if res.text.strip() == "": return True 
        return res.json()
    except Exception as e:
        print(f"Erro API Base44: {e}")
        return []

# ==========================================
# 3. O CÉREBRO: A IA GESTORA (A CADA 25 MINUTOS)
# ==========================================
def reuniao_com_ia_gestora(usuario, preco_atual, rsi_atual):
    # CORREÇÃO AQUI: Avisando ao Python logo de cara que vamos usar a variável global
    global historico_hora 

    uid = usuario.get("usuario_id")
    id_banco = usuario.get("id") # Pega o ID da linha do usuário na Base44
    rsi_antigo = usuario.get("rsi_alvo_compra", 35)
    
    print(f"\n" + "="*50)
    print(f"🧠 [IA GESTORA] Iniciando análise de 25 min para: {uid}")
    
    # Garante que o histórico existe para não quebrar o código
    if uid not in historico_hora:
        historico_hora[uid] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
        
    hist = historico_hora[uid]
    
    prompt = f"""
    Você é o Gestor Quantitativo do robô MiraQuantIA para o usuário {uid}.
    Estamos operando {SYMBOL}. Preço atual: {preco_atual:.2f}. RSI atual: {rsi_atual:.2f}.
    
    RESULTADOS DOS ÚLTIMOS 25 MINUTOS:
    - O robô operário estava configurado para comprar se o RSI caísse para {rsi_antigo}.
    - Operações Reais que bateram a meta: {hist['reais_vitorias']}
    - Operações Reais que deram prejuízo: {hist['reais_derrotas']}
    
    OPORTUNIDADES IGNORADAS (Shadow Trading):
    - Entradas ignoradas que TERIAM DADO LUCRO: {hist['fantasma_vitorias']}
    - Entradas ignoradas que TERIAM DADO PREJUÍZO: {hist['fantasma_derrotas']}
    
    TAREFA:
    Ajuste o 'rsi_alvo_compra' para otimizar os lucros do usuário.
    Responda APENAS um JSON válido neste formato exato:
    {{"rsi_alvo_compra": 40, "observacao_ia": "Sua justificativa."}}
    """
    
    try:
        res = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        limpo = res.text.replace("```json", "").replace("```", "").strip()
        nova_regra = json.loads(limpo)
        
        # Envia a nova regra e a mensagem escrita direto para a Base44!
        payload_atualizacao = {
            "rsi_alvo_compra": nova_regra["rsi_alvo_compra"],
            "observacao_ia": nova_regra["observacao_ia"]
        }
        api_base44("PUT", ENDPOINTS["controle"], payload_atualizacao, id_registro=id_banco)
        
        print(f"✅ [IA GESTORA] Regra enviada para o Dashboard! Novo RSI: {nova_regra['rsi_alvo_compra']}")
        print(f"📝 Justificativa: {nova_regra['observacao_ia']}")
        print("="*50 + "\n")
        
        # Zera o placar para começar o novo ciclo de 25 minutos
        historico_hora[uid] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
        
    except Exception as e:
        print(f"❌ [ERRO IA GESTORA] Falha ao atualizar: {e}")

# ==========================================
# 4. O OPERÁRIO: LOOP PRINCIPAL (A CADA 1 MIN)
# ==========================================
def iniciar_loop():
    global ultima_reuniao_ia, ordens_fantasma, historico_hora
    print("🚀 MIRAQUANTIA V3 - ARQUITETURA MESTRE-OPERÁRIO INTEGRADA À BASE44")
    
    while True:
        try:
            configs = api_base44("GET", ENDPOINTS["controle"])
            if not configs:
                print("⏳ Aguardando usuários na Base44...")
                time.sleep(60)
                continue

            ex = ccxt.bybit({'options': {'defaultType': 'spot'}})
            try:
                preco = ex.fetch_ticker(SYMBOL)['last']
            except Exception as e:
                print(f"Erro ao ler Bybit: {e}")
                time.sleep(60)
                continue
                
            rsi = 45 # Usando RSI 45 como exemplo para forçar simulações rápidas
            
            for user in configs:
                uid = user.get("usuario_id")
                if not user.get("status_bot"): continue
                
                # Inicializa a memória do usuário
                if uid not in ultima_reuniao_ia: ultima_reuniao_ia[uid] = 0
                if uid not in ordens_fantasma: ordens_fantasma[uid] = []
                if uid not in historico_hora: historico_hora[uid] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}

                # 1. Verifica se deu 25 minutos (1500 segundos) para este usuário
                if time.time() - ultima_reuniao_ia[uid] > 1500:
                    reuniao_com_ia_gestora(user, preco, rsi)
                    ultima_reuniao_ia[uid] = time.time()
                    
                    # Atualiza o limite_rsi na memória imediatamente após a IA mudar
                    user['rsi_alvo_compra'] = api_base44("GET", ENDPOINTS["controle"], id_registro=user.get("id")).get("rsi_alvo_compra", 35)

                limite_rsi = user.get("rsi_alvo_compra", 35)
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Operário ({uid}): Preço={preco} | RSI={rsi} | Regra: Comprar no RSI {limite_rsi}")

                # 2. Rastreamento das Ordens Fantasmas
                for ordem in ordens_fantasma[uid][:]:
                    if preco >= ordem['preco_alvo']:
                        historico_hora[uid]['fantasma_vitorias'] += 1
                        ordens_fantasma[uid].remove(ordem)
                        print(f"👻 [FANTASMA] {uid} - Oportunidade ignorada deu LUCRO!")
                    elif preco <= ordem['preco_stop']:
                        historico_hora[uid]['fantasma_derrotas'] += 1
                        ordens_fantasma[uid].remove(ordem)
                        print(f"👻 [FANTASMA] {uid} - Oportunidade ignorada deu PREJUÍZO.")

                # 3. Decisão do Operário
                if rsi <= limite_rsi:
                    print(f"⚠️ [MERCADO] {uid} - RSI atingiu a meta ({limite_rsi})! Robô executando COMPRA!")
                    # TODO: Adicionar lógica real de compra e envio para HistoricoOperacoes
                
                # 4. Criação de Ordem Fantasma (Se chegou a 15 pontos de distância da meta)
                elif rsi <= (limite_rsi + 15):
                    ordens_fantasma[uid].append({
                        "preco_entrada": preco,
                        "preco_alvo": preco * 1.02, # Alvo de 2%
                        "preco_stop": preco * 0.95  # Stop de 5%
                    })
                    print(f"👁️ [OLHEIRO] {uid} - RSI em {rsi}. Registrado como Fantasma para análise futura.")

            time.sleep(60)

        except Exception as e:
            print(f"Erro no ciclo principal: {e}")
            time.sleep(60)

if __name__ == "__main__":
    iniciar_loop()
