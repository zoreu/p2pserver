from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse
import logging
from typing import Dict, Any, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("p2p-proxy")

app = FastAPI(title="P2P Proxy Server")

# Agora peers guarda lista de websockets por peer_id
peers: Dict[str, List[WebSocket]] = {}

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

    # Adiciona websocket à lista do peer_id
    if peer_id not in peers:
        peers[peer_id] = []
    peers[peer_id].append(websocket)

    logger.info(f"Peer {peer_id} conectado. Total conexões desse peer: {len(peers[peer_id])}. Total peers: {len(peers)}")

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            request_id = data.get("request_id")

            if msg_type == "request":
                url = data.get("url")
                method = data.get("method", "GET").upper()
                headers = data.get("headers", {})
                body = data.get("body")
                client_id = data.get("from", peer_id)
                request_type = data.get("request_type", "static")

                if not is_valid_url(url):
                    await websocket.send_json({"type": "error", "message": "URL inválida", "request_id": request_id})
                    continue

                if not request_id:
                    request_id = f"{client_id}_{url[:50]}"

                requests[request_id] = {
                    "client_id": client_id,
                    "source_peer_id": None,
                    "request_type": request_type
                }

                # Escolher qualquer peer que NÃO seja o cliente que fez a requisição
                # Aqui pegamos todos peers exceto o cliente solicitante
                possible_targets = [pid for pid in peers if pid != client_id]
                if not possible_targets:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Nenhum peer disponível para processar a requisição",
                        "request_id": request_id
                    })
                    continue

                # Pode escolher um peer aleatório, aqui escolho o primeiro para simplicidade
                target_peer_id = possible_targets[0]
                requests[request_id]["source_peer_id"] = target_peer_id

                # Envia a requisição para todos sockets do peer "executor"
                for ws in peers[target_peer_id]:
                    await ws.send_json({
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
                # Envia para todos sockets do cliente que solicitou
                if request_id in requests:
                    client_id = requests[request_id]["client_id"]
                    if client_id in peers:
                        for ws in peers[client_id]:
                            await ws.send_json(data)
                    else:
                        logger.warning(f"Peer cliente {client_id} desconectado para request_id {request_id}")

            elif msg_type == "error":
                # Encaminha erro para todos sockets do cliente que solicitou
                if request_id in requests:
                    client_id = requests[request_id]["client_id"]
                    if client_id in peers:
                        for ws in peers[client_id]:
                            await ws.send_json(data)

    except WebSocketDisconnect:
        logger.info(f"Peer {peer_id} desconectado")
    except Exception as e:
        logger.error(f"Erro no websocket do peer {peer_id}: {e}")
    finally:
        # Remove só esse websocket da lista do peer_id
        if peer_id in peers:
            try:
                peers[peer_id].remove(websocket)
                if not peers[peer_id]:  # lista vazia
                    del peers[peer_id]
            except Exception:
                pass

        # Remove requests que envolvam esse peer
        to_remove = [rid for rid, info in requests.items()
                     if info["client_id"] == peer_id or info["source_peer_id"] == peer_id]
        for rid in to_remove:
            del requests[rid]

        logger.info(f"Peer {peer_id} removido/conexão fechada. Peers restantes: {len(peers)}")
