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
    "operacao": f"{BASE_URL}/entities/Operacao" 
}

SYMBOL = 'BTC/USDT'
TIMEFRAME = '1m'

# ==========================================
# 2. MEMÓRIA RAM DO ROBÔ (ESTADOS)
# ==========================================
# Agora guardamos qual operação está aberta para não comprar repetido!
operacoes_abertas = {} 
ordens_fantasma = {}
historico_hora = {}
ultima_reuniao_ia = {}

# ==========================================
# 3. FUNÇÕES DE APOIO E BASE44
# ==========================================
def api_base44(metodo, endpoint, dados=None, id_registro=None):
    headers = {"Content-Type": "application/json", "api_key": BASE44_API_KEY}
    url = f"{endpoint}/{id_registro}" if id_registro else endpoint
    try:
        if metodo == "GET": res = requests.get(url, headers=headers)
        elif metodo == "POST": res = requests.post(url, json=dados, headers=headers)
        elif metodo == "PUT": res = requests.put(url, json=dados, headers=headers)
        else: return []
        
        if res.status_code not in [200, 201, 204]: return []
        if res.text.strip() == "": return True 
        return res.json()
    except:
        return []

def registrar_compra_painel(usuario, preco, categoria="Demo"):
    """Abre a operação e retorna o ID gerado pelo banco para podermos fechar depois"""
    payload = {
        "usuario_id": usuario.get("usuario_id"),
        "par_moeda": SYMBOL,
        "tipo_ordem": "Compra",       
        "categoria_ordem": categoria, # 'Real', 'Demo' ou 'Fantasma'
        "preco_entrada": preco,
        "data_hora": datetime.now().isoformat(),
        "status": "Aberta" 
    }
    # Retorna a resposta completa da Base44, que contém o ID da linha criada
    return api_base44("POST", ENDPOINTS["operacao"], payload)

def fechar_venda_painel(id_operacao, preco_saida, lucro_pct, lucro_fin):
    """Atualiza a linha exata da compra, colocando o preço de saída e lucro"""
    payload = {
        "preco_saida": preco_saida,
        "lucro_porcentagem": round(lucro_pct, 2),
        "lucro_financeiro": round(lucro_fin, 2),
        "status": "Fechada"
    }
    api_base44("PUT", ENDPOINTS["operacao"], payload, id_registro=id_operacao)

def calcular_rsi_real(exchange):
    """Lê o gráfico de 1 minuto e calcula a força real do mercado"""
    try:
        velas = exchange.fetch_ohlcv(SYMBOL, timeframe='1m', limit=15)
        fechamentos = [v[4] for v in velas]
        ganhos, perdas = [], []
        for i in range(1, len(fechamentos)):
            diff = fechamentos[i] - fechamentos[i-1]
            if diff > 0: ganhos.append(diff)
            else: perdas.append(abs(diff))
        media_ganhos = sum(ganhos) / 14 if ganhos else 0
        media_perdas = sum(perdas) / 14 if perdas else 0
        if media_perdas == 0: return 100
        rs = media_ganhos / media_perdas
        return 100 - (100 / (1 + rs))
    except:
        return 50 # Se a corretora falhar, assume mercado neutro

# ==========================================
# 4. O CÉREBRO: A IA GESTORA (A CADA 25 MINUTOS)
# ==========================================
def reuniao_com_ia_gestora(usuario, preco_atual, rsi_atual):
    global historico_hora 
    uid = usuario.get("usuario_id")
    id_banco = usuario.get("id") 
    rsi_antigo = usuario.get("rsi_alvo_compra", 35)
    
    print(f"\n" + "="*50)
    print(f"🧠 [IA GESTORA] Análise em andamento para: {uid}")
    
    if uid not in historico_hora:
        historico_hora[uid] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
        
    hist = historico_hora[uid]
    
    prompt = f"""
    MiraQuantIA Gestor para o usuário {uid}. Ativo: {SYMBOL}. Preço: {preco_atual:.2f}. RSI: {rsi_atual:.2f}.
    RSI Alvo Antigo: {rsi_antigo}.
    VITÓRIAS REAIS: {hist['reais_vitorias']} | DERROTAS REAIS: {hist['reais_derrotas']}
    VITÓRIAS FANTASMAS (Ignoradas que dariam lucro): {hist['fantasma_vitorias']} | DERROTAS FANTASMAS: {hist['fantasma_derrotas']}
    
    Se os Fantasmas têm muitas vitórias, aumente o RSI alvo para operar mais. Se tiver derrotas, diminua.
    Responda APENAS JSON: {{"rsi_alvo_compra": 40, "observacao_ia": "Sua justificativa."}}
    """
    try:
        res = cliente_ia.models.generate_content(model=MODELO_GEMINI, contents=prompt)
        limpo = res.text.replace("```json", "").replace("```", "").strip()
        nova_regra = json.loads(limpo)
        
        api_base44("PUT", ENDPOINTS["controle"], nova_regra, id_registro=id_banco)
        print(f"✅ Regra enviada! Novo RSI: {nova_regra['rsi_alvo_compra']} | {nova_regra['observacao_ia']}")
        historico_hora[uid] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}
        print("="*50 + "\n")
    except Exception as e:
        print(f"❌ Erro IA Gestora: {e}")

# ==========================================
# 5. O OPERÁRIO: LOOP PRINCIPAL (A CADA 1 MIN)
# ==========================================
def iniciar_loop():
    global ultima_reuniao_ia, ordens_fantasma, historico_hora, operacoes_abertas
    print("🚀 MIRAQUANTIA V4 - COM LÓGICA DE COMPRA/VENDA E TRAVA ATIVA")
    
    while True:
        try:
            configs = api_base44("GET", ENDPOINTS["controle"])
            if not configs:
                time.sleep(60)
                continue

            ex = ccxt.bybit({'options': {'defaultType': 'spot'}})
            try:
                preco = ex.fetch_ticker(SYMBOL)['last']
                rsi = calcular_rsi_real(ex) # <-- Agora é RSI REAL DA CORRETORA!
            except:
                time.sleep(60)
                continue
            
            for user in configs:
                uid = user.get("usuario_id")
                
                # Procura se o usuário marcou Demo ou Real. Se não existir o botão, o padrão é Demo.
                modo_operacao = str(user.get("modo_operacao", "Demo")).capitalize() 
                
                # Capital de simulação = $100. Se quiser, podemos ler do 'saldo_atual' depois.
                capital_operacao = 100.0 

                if not user.get("status_bot"): continue
                
                if uid not in ultima_reuniao_ia: ultima_reuniao_ia[uid] = 0
                if uid not in ordens_fantasma: ordens_fantasma[uid] = []
                if uid not in historico_hora: historico_hora[uid] = {"reais_vitorias": 0, "reais_derrotas": 0, "fantasma_vitorias": 0, "fantasma_derrotas": 0}

                # 1. Reunião da IA a cada 25 minutos
                if time.time() - ultima_reuniao_ia[uid] > 1500:
                    reuniao_com_ia_gestora(user, preco, rsi)
                    ultima_reuniao_ia[uid] = time.time()
                    user['rsi_alvo_compra'] = api_base44("GET", ENDPOINTS["controle"], id_registro=user.get("id")).get("rsi_alvo_compra", 35)

                limite_rsi = user.get("rsi_alvo_compra", 35)
                
                # ------------------------------------------------------------------
                # LÓGICA DE VENDA (Se tiver operação aberta)
                # ------------------------------------------------------------------
                if uid in operacoes_abertas:
                    op = operacoes_abertas[uid]
                    lucro_pct = ((preco - op["preco_entrada"]) / op["preco_entrada"]) * 100
                    lucro_fin = capital_operacao * (lucro_pct / 100)
                    
                    print(f"👁️ [{uid}] Acompanhando Ordem... Entrada: {op['preco_entrada']} | Atual: {preco} | Lucro: {lucro_pct:.2f}%")
                    
                    # Vende se bater +1% de Meta ou -1% de Risco (Você pode alterar esses números)
                    if lucro_pct >= 1.0 or lucro_pct <= -1.0:
                        print(f"💰 [{uid}] FECHANDO OPERAÇÃO! Lucro/Prejuízo da vez: {lucro_pct:.2f}%")
                        
                        fechar_venda_painel(op["id_banco"], preco, lucro_pct, lucro_fin)
                        
                        if lucro_pct > 0: historico_hora[uid]['reais_vitorias'] += 1
                        else: historico_hora[uid]['reais_derrotas'] += 1
                        
                        del operacoes_abertas[uid] # Libera o robô para comprar de novo!
                        
                # ------------------------------------------------------------------
                # LÓGICA DE COMPRA (Se estiver com a carteira vazia)
                # ------------------------------------------------------------------
                else:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] {uid} ({modo_operacao}): Preço={preco} | RSI={rsi:.2f} | Meta RSI={limite_rsi}")

                    # COMPRA REAL OU DEMO
                    if rsi <= limite_rsi:
                        print(f"⚠️ RSI bateu a meta ({limite_rsi})! EXECUTANDO COMPRA NO MODO {modo_operacao}!")
                        
                        # Retorna a linha completa que foi criada na Base44
                        resultado_banco = registrar_compra_painel(user, preco, categoria=modo_operacao)
                        
                        # Salva o ID na memória para sabermos qual linha fechar na hora da venda
                        if resultado_banco and "id" in resultado_banco:
                            operacoes_abertas[uid] = {
                                "id_banco": resultado_banco["id"],
                                "preco_entrada": preco
                            }

                    # FANTASMA (Só acompanha)
                    elif rsi <= (limite_rsi + 10):
                        ordens_fantasma[uid].append({"preco_entrada": preco, "preco_alvo": preco * 1.01, "preco_stop": preco * 0.99})
                        registrar_compra_painel(user, preco, categoria="Fantasma")

                # Checagem dos Fantasmas (Apenas memória)
                for ordem in ordens_fantasma[uid][:]:
                    if preco >= ordem['preco_alvo']:
                        historico_hora[uid]['fantasma_vitorias'] += 1
                        ordens_fantasma[uid].remove(ordem)
                    elif preco <= ordem['preco_stop']:
                        historico_hora[uid]['fantasma_derrotas'] += 1
                        ordens_fantasma[uid].remove(ordem)

            time.sleep(60)

        except Exception as e:
            print(f"Erro no ciclo: {e}")
            time.sleep(60)

if __name__ == "__main__":
    iniciar_loop()
