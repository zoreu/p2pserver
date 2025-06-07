from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse
import asyncio
import base64
import logging
from typing import Dict, Any
import uvicorn

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="P2P Proxy Server")

# Armazena peers conectados e requisições ativas
peers: Dict[str, WebSocket] = {}  # {peer_id: websocket}
requests: Dict[str, Dict[str, Any]] = {}  # {request_id: {client_id, source_peer_id, request_type}}

# Rota de boas-vindas (JSON)
@app.get("/")
async def welcome():
    """Rota de boas-vindas retornando uma mensagem JSON."""
    return JSONResponse({
        "message": "Bem-vindo ao Servidor P2P Proxy!",
        "version": "1.0.0",
        "description": "Servidor para coordenação de requisições P2P, incluindo streams MPEG-TS, M3U8 e requisições HTTP (GET/POST).",
        "status": "running",
        "active_peers": len(peers)
    })

# Rota de boas-vindas alternativa (HTML)
@app.get("/welcome", response_class=HTMLResponse)
async def welcome_html():
    """Rota de boas-vindas retornando uma página HTML."""
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Bem-vindo ao P2P Proxy Server</title>
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
            h1 { color: #333; }
            p { font-size: 18px; color: #666; }
        </style>
    </head>
    <body>
        <h1>Bem-vindo ao P2P Proxy Server!</h1>
        <p>Este servidor coordena requisições P2P para streams (MPEG-TS, M3U8) e requisições HTTP (GET/POST).</p>
        <p>Peers ativos: <strong>{}</strong></p>
        <p>Para mais detalhes, acesse a documentação em <a href="/docs">/docs</a>.</p>
    </body>
    </html>
    """.format(len(peers))
    return HTMLResponse(content=html_content)

# Função para validar URLs (básica, para segurança)
def is_valid_url(url: str) -> bool:
    import re
    try:
        return bool(re.match(r'^https?://[^\s/$.?#].[^\s]*$', url))
    except:
        return False

@app.websocket("/ws/{peer_id}")
async def websocket_endpoint(websocket: WebSocket, peer_id: str):
    """Gerencia conexões WebSocket dos peers."""
    await websocket.accept()
    peers[peer_id] = websocket
    logger.info(f"Peer {peer_id} conectado. Total de peers: {len(peers)}")
    
    try:
        while True:
            data = await websocket.receive_json()
            request_type = data.get("request_type")  # "static" ou "stream"
            request_id = data.get("request_id", f"{data.get('from', peer_id)}_{data.get('url', '')[:50]}")

            if data.get("type") == "request":
                url = data.get("url")
                client_id = data.get("from")
                method = data.get("method", "GET").upper()
                headers = data.get("headers", {})
                body = data.get("body")
                
                if not is_valid_url(url):
                    logger.error(f"URL inválida: {url}")
                    if client_id in peers:
                        await peers[client_id].send_json({
                            "type": "error",
                            "request_id": request_id,
                            "message": "URL inválida"
                        })
                    continue

                # Registra a requisição
                requests[request_id] = {
                    "client_id": client_id,
                    "source_peer_id": None,
                    "request_type": request_type
                }

                # Escolhe um peer desbloqueado
                target_peer = next((p for p in peers if p != client_id), None)
                if target_peer:
                    requests[request_id]["source_peer_id"] = target_peer
                    logger.info(f"Encaminhando requisição {request_id} para peer {target_peer}")
                    await peers[target_peer].send_json({
                        "type": "fetch",
                        "url": url,
                        "request_id": request_id,
                        "request_type": request_type,
                        "method": method,
                        "headers": headers,
                        "body": body
                    })
                else:
                    logger.warning(f"Nenhum peer disponível para {request_id}")
                    if client_id in peers:
                        await peers[client_id].send_json({
                            "type": "error",
                            "request_id": request_id,
                            "message": "Nenhum peer disponível"
                        })

            elif data.get("type") == "response":
                if request_id in requests:
                    client_id = requests[request_id]["client_id"]
                    if client_id in peers:
                        logger.info(f"Enviando resposta para {client_id} (request_id: {request_id})")
                        await peers[client_id].send_json(data)
                    else:
                        logger.warning(f"Cliente {client_id} não encontrado para {request_id}")

            elif data.get("type") == "stream_chunk":
                if request_id in requests:
                    client_id = requests[request_id]["client_id"]
                    if client_id in peers:
                        await peers[client_id].send_json({
                            "type": "stream_chunk",
                            "request_id": request_id,
                            "chunk": data.get("chunk")
                        })
                    else:
                        logger.warning(f"Cliente {client_id} não encontrado para stream {request_id}")

            elif data.get("type") == "error":
                if request_id in requests:
                    client_id = requests[request_id]["client_id"]
                    if client_id in peers:
                        await peers[client_id].send_json(data)

    except WebSocketDisconnect:
        logger.info(f"Peer {peer_id} desconectado")
    except Exception as e:
        logger.error(f"Erro no WebSocket para {peer_id}: {e}")
    finally:
        if peer_id in peers:
            del peers[peer_id]
        requests_to_remove = [rid for rid, info in requests.items() if info["source_peer_id"] == peer_id or info["client_id"] == peer_id]
        for rid in requests_to_remove:
            del requests[rid]
        logger.info(f"Peer {peer_id} removido. Total de peers: {len(peers)}")

@app.post("/request")
async def http_request(data: dict):
    """Endpoint HTTP para receber requisições iniciais."""
    url = data.get("url")
    peer_id = data.get("peer_id")
    request_type = data.get("request_type", "static")
    method = data.get("method", "GET")
    headers = data.get("headers", {})
    body = data.get("body")
    
    if not is_valid_url(url):
        return JSONResponse({"status": "error", "message": "URL inválida"}, status_code=400)
    
    if peer_id not in peers:
        return JSONResponse({"status": "error", "message": "Peer não conectado"}, status_code=400)
    
    request_id = f"{peer_id}_{url[:50]}"
    requests[request_id] = {
        "client_id": peer_id,
        "source_peer_id": None,
        "request_type": request_type
    }
    
    target_peer = next((p for p in peers if p != peer_id), None)
    if target_peer:
        requests[request_id]["source_peer_id"] = target_peer
        await peers[target_peer].send_json({
            "type": "fetch",
            "url": url,
            "request_id": request_id,
            "request_type": request_type,
            "method": method,
            "headers": headers,
            "body": body
        })
        return JSONResponse({"status": "ok", "request_id": request_id})
    else:
        return JSONResponse({"status": "error", "message": "Nenhum peer disponível"}, status_code=503)

# if __name__ == "__main__":
#     uvicorn.run(app, host="0.0.0.0", port=8000, ws_max_size=100_000_000)