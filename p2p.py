from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse
import logging
from typing import Dict, Any, List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("p2p-proxy")

app = FastAPI(title="P2P Proxy Server")

# Armazena peers conectados: {peer_id: [websocket, websocket, ...]}
peers: Dict[str, List[WebSocket]] = {}
peer_list = {}

# Armazena requests pendentes: {request_id: {client_id, source_peer_id, request_type}}
requests: Dict[str, Dict[str, Any]] = {}

def is_valid_url(url: str) -> bool:
    import re
    try:
        return bool(re.match(r'^https?://[^\s/$.?#].[^\s]*$', url))
    except:
        return False

async def send_to_peer(peer_id: str, message: dict, exclude_ws: WebSocket = None):
    if peer_id not in peers:
        return
    to_remove = []
    for ws in peers[peer_id]:
        if ws == exclude_ws:
            continue
        try:
            await ws.send_json(message)
        except Exception as e:
            logger.warning(f"Erro ao enviar mensagem para peer {peer_id}: {e}")
            to_remove.append(ws)
    for ws in to_remove:
        if ws in peers[peer_id]:
            peers[peer_id].remove(ws)
    if not peers[peer_id]:
        del peers[peer_id]

@app.get("/peers")
async def list_peers():
    logger.info(f"Peers ativos: {list(peer_list.keys())}")
    return JSONResponse(content={"connected_peers": list(peer_list.keys())})

@app.get("/")
async def home():
    return HTMLResponse(content=f"""
    <h1>Peers conectados</h1>
    <ul>
        {''.join(f"<li>{peer}</li>" for peer in list(peer_list.keys()))}
    </ul>
    """)

@app.websocket("/ws/{peer_id}")
async def websocket_endpoint(websocket: WebSocket, peer_id: str):
    await websocket.accept()
    peers.setdefault(peer_id, []).append(websocket)
    logger.info(f"Peer {peer_id} conectado. Total peers: {sum(len(v) for v in peers.values())}")
    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                logger.info(f"Peer {peer_id} desconectado")
                try:
                    del peer_list[peer_id]
                except:
                    pass
                if peer_id in peers and websocket in peers[peer_id]:
                    peers[peer_id].remove(websocket)
                    if not peers[peer_id]:
                        del peers[peer_id]
                to_remove = [rid for rid, info in requests.items() if info["client_id"] == peer_id or info["source_peer_id"] == peer_id]
                for rid in to_remove:
                    del requests[rid]
                logger.info(f"Peer {peer_id} removido. Peers restantes: {sum(len(v) for v in peers.values())}")                
                break
            except Exception as e:
                logger.warning(f"Erro ao processar mensagem do peer {peer_id}: {e}")
                continue

            peer_list[peer_id] = 'websocket'

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

                target_peer_id = data.get("target_peer_id")
                if target_peer_id:
                    if target_peer_id not in peers or not peers[target_peer_id]:
                        await websocket.send_json({
                            "type": "error",
                            "message": f"O peer de destino '{target_peer_id}' não está disponível",
                            "request_id": request_id
                        })
                        continue
                else:
                    target_peer_id = next((p for p in peers if p != client_id), None)
                    if not target_peer_id:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Nenhum peer disponível para processar a requisição",
                            "request_id": request_id
                        })
                        continue

                requests[request_id]["source_peer_id"] = target_peer_id

                await send_to_peer(target_peer_id, {
                    "type": "fetch",
                    "url": url,
                    "method": method,
                    "headers": headers,
                    "body": body,
                    "request_id": request_id,
                    "request_type": request_type,
                    "from": client_id
                }, exclude_ws=websocket)

            elif msg_type == "response":
                if request_id in requests:
                    client_id = requests[request_id]["client_id"]
                    await send_to_peer(client_id, data)

            elif msg_type == "error":
                if request_id in requests:
                    client_id = requests[request_id]["client_id"]
                    await send_to_peer(client_id, data)

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong", "request_id": request_id})

    except Exception as e:
        logger.error(f"Erro inesperado no websocket do peer {peer_id}: {e}")

    # finally:
    #     #if peer_id in peers_list:
    #     #   peers_list.remove(peer_id)
    #     if peer_id in peers and websocket in peers[peer_id]:
    #         peers[peer_id].remove(websocket)
    #         if not peers[peer_id]:
    #             del peers[peer_id]
    #     to_remove = [rid for rid, info in requests.items() if info["client_id"] == peer_id or info["source_peer_id"] == peer_id]
    #     for rid in to_remove:
    #         del requests[rid]
    #     logger.info(f"Peer {peer_id} removido. Peers restantes: {sum(len(v) for v in peers.values())}")
