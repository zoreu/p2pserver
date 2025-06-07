from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse
import logging
from typing import Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("p2p-proxy")

app = FastAPI(title="P2P Proxy Server")

# Armazena peers conectados: {peer_id: websocket}
peers: Dict[str, WebSocket] = {}

# Armazena requests pendentes: {request_id: {client_id, source_peer_id, request_type}}
requests: Dict[str, Dict[str, Any]] = {}

def is_valid_url(url: str) -> bool:
    import re
    try:
        return bool(re.match(r'^https?://[^\s/$.?#].[^\s]*$', url))
    except:
        return False

@app.websocket("/ws/{peer_id}")
async def websocket_endpoint(websocket: WebSocket, peer_id: str):
    await websocket.accept()
    peers[peer_id] = websocket
    logger.info(f"Peer {peer_id} conectado. Total peers: {len(peers)}")

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            request_id = data.get("request_id")

            if msg_type == "request":
                # Cliente A envia requisição para acessar URL
                url = data.get("url")
                method = data.get("method", "GET").upper()
                headers = data.get("headers", {})
                body = data.get("body")
                client_id = data.get("from", peer_id)
                request_type = data.get("request_type", "static")

                if not is_valid_url(url):
                    await websocket.send_json({"type": "error", "message": "URL inválida", "request_id": request_id})
                    continue

                # Criar request_id único se não veio
                if not request_id:
                    request_id = f"{client_id}_{url[:50]}"

                # Armazena a requisição
                requests[request_id] = {
                    "client_id": client_id,
                    "source_peer_id": None,
                    "request_type": request_type
                }

                # Escolhe peer que NÃO seja o cliente que fez a requisição
                target_peer_id = next((p for p in peers if p != client_id), None)

                if not target_peer_id:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Nenhum peer disponível para processar a requisição",
                        "request_id": request_id
                    })
                    continue

                requests[request_id]["source_peer_id"] = target_peer_id

                # Envia a requisição para o peer "executor"
                await peers[target_peer_id].send_json({
                    "type": "fetch",
                    "url": url,
                    "method": method,
                    "headers": headers,
                    "body": body,
                    "request_id": request_id,
                    "request_type": request_type,
                    "from": client_id
                })

            elif msg_type == "response":
                # Peer "executor" responde para o peer solicitante
                if request_id in requests:
                    client_id = requests[request_id]["client_id"]
                    if client_id in peers:
                        await peers[client_id].send_json(data)
                    else:
                        logger.warning(f"Peer cliente {client_id} desconectado para request_id {request_id}")

            elif msg_type == "error":
                # Encaminha erro para o cliente que solicitou
                if request_id in requests:
                    client_id = requests[request_id]["client_id"]
                    if client_id in peers:
                        await peers[client_id].send_json(data)

    except WebSocketDisconnect:
        logger.info(f"Peer {peer_id} desconectado")
    except Exception as e:
        logger.error(f"Erro no websocket do peer {peer_id}: {e}")
    finally:
        if peer_id in peers:
            del peers[peer_id]
        # Limpa requests que envolvem este peer
        to_remove = [rid for rid, info in requests.items() if info["client_id"] == peer_id or info["source_peer_id"] == peer_id]
        for rid in to_remove:
            del requests[rid]
        logger.info(f"Peer {peer_id} removido. Peers restantes: {len(peers)}")

