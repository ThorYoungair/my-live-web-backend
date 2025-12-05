# -*- coding: utf-8 -*-
import uvicorn
import json
import asyncio
import re
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from bilibili_api import live, Credential
import aiohttp
import uuid
import time
from typing import Optional

# ==========================================
# 🔐 配置区域 (请替换为您自己的 SESSDATA)
# ==========================================
SESSDATA = "0d5ceb32%2C1779919308%2Ca276a%2Ab1CjCr1DByEwubcFGNC3jSZC18fEm4MgMO-3b2yE5CSquh_pZ8_jQ8esjl1MaTj_W59QUSVndxRkpSUEE5TjVDOXU0ZkJXamtrUnBlalNhTm5zZ0RBQm5zWXBJTm94SFpkQzU4bmg2Z21fbFJ6Z1RHRVBSSndmckI2WTZlOHY3M096YWhXVlJocVN3IIEC"
CREDENTIAL = Credential(sessdata=SESSDATA)

# ==========================================
# ⬇️ 抖音 ProtoBuf 模拟辅助函数 (云端友好)
# ==========================================

def encode_douyin_ws_frame(log_id: str, payload: bytes = b'') -> bytes:
    """
    占位函数: 构造抖音 WebSocket 客户端帧。
    """
    # 极简心跳包体: 实际 ProtoBuf 编码，这里只返回空字节或心跳标识
    # 实际的 ProtoBuf 消息头包含了 LogID、Service 和 Method
    
    # 假设心跳包 Payload 是空的
    return payload


def decode_douyin_ws_frame(data: bytes) -> dict:
    """
    占位函数: 模拟解析抖音 ProtoBuf 帧。
    ---
    TODO: 真正的抖音弹幕解码和解析 ProtoBuf 必须在此处实现。
    ---
    """
    # 极简返回结构，提示用户需要解析
    return {
        "messages": [{
            "type": "danmaku",
            "text": f"[抖音 ProtoBuf 待解析，字节大小: {len(data)}]",
            "user": "System_User"
        }],
        "log_id": f"LogId_{uuid.uuid4()}",
        "decoded": False
    }


# ==========================================
# 🌐 FastAPI 初始化
# ==========================================
app = FastAPI()
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

@app.get("/")
def read_root(): return {"status": "running"}

# --- 直播流解析 (使用 Streamlink) ---
import streamlink
@app.get("/api/play")
def get_stream(url: str):
    try:
        clean_url = url.split('?')[0]
        session = streamlink.Streamlink()
        session.set_option("http-headers", {'Cookie': f'SESSDATA={SESSDATA}'})
        streams = session.streams(clean_url)
        if not streams: return {"status": "error", "message": "未找到流"}
        
        quality_map = {}
        for q, s in streams.items():
            try:
                if hasattr(s, 'url'): quality_map[q] = s.url
                elif hasattr(s, 'to_url'): quality_map[q] = s.to_url()
            except: continue
            
        default = 'best' if 'best' in quality_map else list(quality_map.keys())[0]
        return {"status": "success", "default_quality": default, "qualities": quality_map}
    except Exception as e: return {"status": "error", "message": str(e)}

@app.get("/api/check")
def check_status(url: str):
    try: 
        clean_url = url.split('?')[0]
        session = streamlink.Streamlink()
        session.set_option("http-headers", {'Cookie': f'SESSDATA={SESSDATA}'})
        return {"is_live": bool(session.streams(clean_url))}
    except: return {"is_live": False}


# ==========================================
# ⬇️ B站弹幕代理 (完整逻辑)
# ==========================================

async def start_bilibili_room(room_id, websocket: WebSocket):
    print(f"🚀 [B站] 正在连接: {room_id}")
    room = live.LiveDanmaku(room_id, credential=CREDENTIAL)

    @room.on('DANMU_MSG')
    async def on_danmaku(event):
        try:
            content = event['data']['info'][1]
            user_name = event['data']['info'][2][1]
            await websocket.send_text(json.dumps({
                "type": "danmaku",
                "text": content,
                "user": user_name,
                "platform": "bilibili"
            }))
        except:
            raise WebSocketDisconnect()

    connect_task = asyncio.create_task(room.connect())

    try:
        while True:
            await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            if connect_task.done() and connect_task.exception():
                print(f"❌ B站任务异常: {connect_task.exception()}")
                break

    except WebSocketDisconnect:
        print("🔌 B站: 前端断开，停止接收弹幕")
    except asyncio.TimeoutError:
         pass
    except Exception as e:
        print(f"❌ B站异常中断: {e}")
    finally:
        print("🧹 清理 B站 资源...")
        try:
            connect_task.cancel()
            await room.disconnect()
        except: pass

# ==========================================
# ⬇️ 抖音弹幕代理 (纯 WebSocket 架构)
# ==========================================

async def start_douyin_room(url: str, websocket: WebSocket):
    # 1. 提取房间 ID (需要从 URL 中稳定提取，这里是简化版)
    match = re.search(r'(live|v)/([a-zA-Z0-9]+)', url)
    room_id = match.group(2) if match else None
    
    if not room_id:
        await websocket.send_text(json.dumps({"type": "system", "text": "抖音: 无法解析房间ID"}))
        return

    print(f"🚀 [抖音] 正在连接: {room_id}")

    # --- TODO 1: 获取 WebSocket 地址和 Headers (实现难度最高) ---
    # 必须通过 HTTP 请求模拟浏览器获取最新的 ttwid, ac_nonce 等关键参数。
    # 
    # Placeholder URL:
    DOUYIN_WS_BASE = "wss://webcast-ws-web-lf.douyin.com/ws/room/?compress=lz4&version=1.0.0" 
    DOUYIN_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36',
        'Referer': f'https://live.douyin.com/{room_id}',
        'Cookie': 'YOUR_VALID_COOKIE_HERE', # 关键
    }
    # -------------------------------------------------------------
    
    try:
        async with aiohttp.ClientSession(headers=DOUYIN_HEADERS) as session:
            async with session.ws_connect(DOUYIN_WS_BASE, timeout=15) as ws:
                
                await websocket.send_text(json.dumps({"type": "system", "text": "抖音: 连接成功，等待 ProtoBuf 握手..."}))
                
                # --- TODO 2: 发送房间认证/握手消息 (ProtoBuf) ---
                # auth_payload = create_douyin_auth_protobuf(room_id)
                # await ws.send_bytes(encode_douyin_ws_frame(log_id="auth_init", payload=auth_payload))
                # ---------------------------------------------------
                
                # 启动心跳任务 (每 10 秒)
                heartbeat_task = asyncio.create_task(
                    _douyin_heartbeat_sender(ws)
                )

                while True:
                    # 并行等待前端探活和抖音 WS 消息
                    douyin_msg_task = asyncio.create_task(ws.receive())
                    frontend_probe_task = asyncio.create_task(websocket.receive_text())
                    
                    done, pending = await asyncio.wait(
                        [douyin_msg_task, frontend_probe_task],
                        timeout=5, # 5秒内必须有消息或前端探活
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    # 检查前端是否断开
                    if frontend_probe_task in done:
                        try: await frontend_probe_task
                        except: raise WebSocketDisconnect()
                    
                    # 处理抖音消息
                    if douyin_msg_task in done:
                        msg = await douyin_msg_task
                        
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            data = decode_douyin_ws_frame(msg.data)
                            
                            # 将解析后的弹幕转发给前端
                            for danmaku_msg in data.get('messages', []):
                                await websocket.send_text(json.dumps({
                                    "type": "danmaku",
                                    "text": danmaku_msg['text'],
                                    "user": danmaku_msg['user'],
                                    "platform": "douyin"
                                }))
                        
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            print("❌ 抖音 WebSocket 已关闭")
                            raise WebSocketDisconnect()
                    
                    # 取消所有等待中的任务
                    for task in pending:
                        task.cancel()

    except aiohttp.ClientConnectorError as e:
        await websocket.send_text(json.dumps({"type": "system", "text": f"抖音: 连接失败 (网络/地址错误)"}))
        print(f"❌ 抖音连接异常: {e}")
    except WebSocketDisconnect:
        print("🔌 抖音: 前端断开，停止接收弹幕")
    except Exception as e:
        await websocket.send_text(json.dumps({"type": "system", "text": f"抖音: 发生未处理的错误: {e}"}))
        print(f"❌ 抖音异常中断: {e}")
    finally:
        print("🧹 清理抖音资源...")
        if 'heartbeat_task' in locals():
             heartbeat_task.cancel()


async def _douyin_heartbeat_sender(ws: WebSocket):
    """
    周期性发送心跳包，保持抖音 WebSocket 连接。
    """
    heartbeat_payload = encode_douyin_ws_frame(log_id="heartbeat", payload=b'') # 空 Payload 模拟心跳
    try:
        while True:
            await asyncio.sleep(10) # 抖音心跳周期通常是 10-20 秒
            if not ws.closed:
                await ws.send_bytes(heartbeat_payload)
            else:
                break
    except asyncio.CancelledError:
        print("❤️ 抖音心跳任务被取消")


# ==========================================
# 🌐 WebSocket 路由
# ==========================================

@app.websocket("/ws/danmaku")
async def ws_endpoint(websocket: WebSocket, url: str):
    await websocket.accept()
    
    if "bilibili" in url:
        try:
            # 简化 B站房间号提取
            short_id = url.split('?')[0].split('/')[-1]
            if short_id.isdigit():
                await start_bilibili_room(int(short_id), websocket)
        except:
             await websocket.send_text(json.dumps({"type": "system", "text": "B站: 房间ID解析失败"}))

    elif "douyin" in url:
        await start_douyin_room(url, websocket)

    else:
        await websocket.send_text(json.dumps({"type": "system", "text": "平台未支持弹幕功能"}))
        try:
            while True: await websocket.receive_text()
        except: pass

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
