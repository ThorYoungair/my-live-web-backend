# -*- coding: utf-8 -*-
import uvicorn
import json
import asyncio
import re
import aiohttp # 用于抖音 WS 连接
import uuid
import time
from typing import Optional
import zlib
import struct 

# 🚨 解决 NameError: 确保导入了 FastAPI 和 CORSMiddleware
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware 

# ⚠️ 导入 ProtoBuf 模块 (占位符，假设已生成 douyin_pb2.py)
try:
    import douyin_pb2 
except ImportError:
    print("❌ douyin_pb2.py 未找到，抖音弹幕功能将无法工作。")
    class PlaceholderPB:
        class Request:
            def SerializeToString(self): return b''
        class Response:
            def ParseFromString(self, data): pass
            @property
            def messages(self): return []
            @property
            def log_id(self): return "N/A"
            @property
            def payload(self): return b''
        class Webcast:
            class Im:
                class PushFrame:
                    SeqID = 0
                    LogID = "N/A"
                    service = 0
                    method = 0
                    payload_encoding = 'none'
                    payload_type = ''
                    payload = b''
                    def SerializeToString(self): return b''
                class Request:
                    room_id = ''
                    device_platform = ''
                    aid = 0
                    def SerializeToString(self): return b''
                class ChatMessage:
                    class User:
                         nickname = ''
                    content = ''
                    user = User()
    douyin_pb2 = PlaceholderPB()
    

# ==========================================
# 🔐 配置区域 
# ==========================================
SESSDATA = "0d5ceb32%2C1779919308%2Ca276a%2Ab1CjCr1DByEwubcFGNC3jSZC18fEm4MgMO-3b2yE5CSquh_pZ8_jQ8esjl1MaTj_W59QUSVndxRkpSUEE5TjVDOXU0ZkJXamtrUnBlalNhTm5zZ0RBQm5zWXBJTm94SFpkQzU4bmg2Z21fbFJ6Z1RHRVBSSndmckI2WTZlOHY3M096YWhXVlJocVN3IIEC"
from bilibili_api import live, Credential
CREDENTIAL = Credential(sessdata=SESSDATA)

# 🎯 修复: 通用 Headers 集合，增强 Streamlink 的多平台解析能力 (视频解析修正)
COMMON_HEADERS = {
    # 使用强大的 User-Agent 模拟浏览器
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Encoding': 'gzip, deflate, br',
    # 保持 B站 SESSDATA，帮助 Streamlink 识别 B站流
    'Cookie': f'SESSDATA={SESSDATA}'
}


# ==========================================
# ⬇️ 抖音 ProtoBuf 核心逻辑辅助函数 (仅占位)
# ==========================================
def get_log_id() -> str:
    return str(uuid.uuid4()).replace('-', '')[0:16] 

def encode_douyin_ws_frame(log_id: str, payload_type: str, payload: bytes) -> bytes:
    # 占位函数，需要 douyin_pb2 才能实现
    push_frame = douyin_pb2.Webcast.Im.PushFrame()
    push_frame.SeqID = int(time.time() * 1000)
    try: push_frame.LogID = int(log_id, 16)
    except: push_frame.LogID = int(time.time() * 1000)
    push_frame.service = 3
    push_frame.method = 4
    push_frame.payload_encoding = 'none'
    push_frame.payload_type = payload_type
    push_frame.payload = payload
    return push_frame.SerializeToString()

def decode_douyin_ws_frame(data: bytes) -> dict:
    # 占位函数，需要 douyin_pb2 才能实现
    push_frame = douyin_pb2.Webcast.Im.PushFrame()
    try:
        push_frame.ParseFromString(data)
    except:
        return {"messages": [], "log_id": "ParseFrameError", "error": "PushFrame解析失败"}

    payload_data = push_frame.payload
    return {
        "messages": [], 
        "log_id": push_frame.LogID, 
        "error": "ProtoBuf解码逻辑未实现"
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

# --- 视频解析 (已修复为通用 Headers) ---
import streamlink
@app.get("/api/play")
def get_stream(url: str):
    try:
        clean_url = url.split('?')[0]
        session = streamlink.Streamlink()
        
        # 🎯 修复: 使用通用且强大的 Headers 集合
        session.set_option("http-headers", COMMON_HEADERS)
        
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
        
        # 🎯 修复: 使用通用且强大的 Headers 集合
        session.set_option("http-headers", COMMON_HEADERS)
        
        return {"is_live": bool(session.streams(clean_url))}
    except: return {"is_live": False}


# ==========================================
# ⬇️ B站弹幕代理 (已恢复并修正兼容性)
# ==========================================

async def start_bilibili_room(room_id, websocket: WebSocket):
    print(f"🚀 [B站] 正在连接: {room_id}")
    
    room = live.LiveDanmaku(room_id, credential=CREDENTIAL)

    @room.on('DANMU_MSG')
    async def on_danmaku(event):
        try:
            content = event['data']['info'][1]
            user_name = event['data']['info'][2][1] # 提取用户名
            print(f"💬 {content}")
            
            # ✅ 恢复用户原始逻辑，并确保兼容性: 转发 content 和 user_name
            # 注意: 我移除了平台字段，因为您的原始工作代码中没有它。
            await websocket.send_text(json.dumps({
                "type": "danmaku",
                "text": content,
                "user": user_name # 恢复用户名
            }))
        except:
            raise WebSocketDisconnect()

    # 启动连接任务
    connect_task = asyncio.create_task(room.connect())

    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                if connect_task.done():
                    print("❌ B站连接意外断开")
                    break
            
            if connect_task.done() and connect_task.exception():
                print(f"❌ B站任务异常: {connect_task.exception()}")
                break

    except WebSocketDisconnect:
        print("🔌 B站: 前端断开，停止接收弹幕")
    except Exception as e:
        print(f"❌ B站异常中断: {e}")
    finally:
        print("🧹 清理 B站 资源...")
        try:
            connect_task.cancel()
            await room.disconnect()
        except: pass

# ==========================================
# ⬇️ 抖音弹幕代理 (ProtoBuf 集成架构)
# ==========================================

async def _douyin_heartbeat_sender(ws: aiohttp.ClientWebSocketResponse):
    # 占位心跳
    heartbeat_request = douyin_pb2.Webcast.Im.Request()
    heartbeat_frame = encode_douyin_ws_frame(
        log_id=get_log_id(),
        payload_type='WebcastRequest', 
        payload=heartbeat_request.SerializeToString()
    )
    
    try:
        while True:
            await asyncio.sleep(10) 
            if not ws.closed:
                await ws.send_bytes(heartbeat_frame)
    except asyncio.CancelledError:
        print("❤️ 抖音心跳任务被取消")


async def start_douyin_room(url: str, websocket: WebSocket):
    # 1. 提取房间 ID
    match = re.search(r'(live|v)/([a-zA-Z0-9]+)', url)
    room_id = match.group(2) if match else None
    
    if not room_id:
        await websocket.send_text(json.dumps({"type": "system", "text": "抖音: 无法解析房间ID"}))
        return

    print(f"🚀 [抖音] 正在连接: {room_id}")

    # --- 1. 获取 WebSocket 地址和 Headers ---
    DOUYIN_WS_BASE = "wss://webcast-ws-web-lf.douyin.com/ws/room/?compress=lz4&version=1.0.0" 
    DOUYIN_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36',
        'Referer': f'https://live.douyin.com/{room_id}',
        'Cookie': 'YOUR_VALID_COOKIE_HERE', # 🚨 请在部署前确保使用有效的 Cookie
    }
    
    try:
        async with aiohttp.ClientSession(headers=DOUYIN_HEADERS) as session:
            async with session.ws_connect(DOUYIN_WS_BASE, timeout=15) as ws:
                
                # --- 2. 构造并发送房间认证请求 ---
                auth_request = douyin_pb2.Webcast.Im.Request()
                auth_request.room_id = room_id
                auth_request.device_platform = "web"
                auth_request.aid = 1128 
                
                auth_frame = encode_douyin_ws_frame(
                    log_id=get_log_id(),
                    payload_type='WebcastRequest',
                    payload=auth_request.SerializeToString()
                )
                await ws.send_bytes(auth_frame)
                await websocket.send_text(json.dumps({"type": "system", "text": "抖音: 连接成功，已发送认证请求。"}))
                
                # 启动心跳任务
                heartbeat_task = asyncio.create_task(
                    _douyin_heartbeat_sender(ws)
                )

                # 5. 循环接收消息
                while True:
                    douyin_msg_task = asyncio.create_task(ws.receive())
                    frontend_probe_task = asyncio.create_task(websocket.receive_text())
                    
                    done, pending = await asyncio.wait(
                        [douyin_msg_task, frontend_probe_task],
                        timeout=5,
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    for task in pending:
                        task.cancel()
                    
                    if frontend_probe_task in done:
                         try: await frontend_probe_task
                         except: raise WebSocketDisconnect()
                    
                    if douyin_msg_task in done:
                        msg = await douyin_msg_task
                        
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            data = decode_douyin_ws_frame(msg.data)
                            
                            if data.get("error"):
                                raise Exception(f"ProtoBuf解码错误: {data['error']}")
                                
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

    except aiohttp.ClientConnectorError:
        await websocket.send_text(json.dumps({"type": "system", "text": "抖音: 连接失败 (网络/地址错误)，请检查 Headers"}))
    except WebSocketDisconnect:
        print("🔌 抖音: 前端断开，停止接收弹幕")
    except Exception as e:
        await websocket.send_text(json.dumps({"type": "system", "text": f"抖音: 发生未处理的错误: {e}"}))
        print(f"❌ 抖音异常中断: {e}")
    finally:
        print("🧹 清理抖音资源...")
        if 'heartbeat_task' in locals() and not heartbeat_task.done():
             heartbeat_task.cancel()


# ==========================================
# 🌐 WebSocket 路由
# ==========================================

@app.websocket("/ws/danmaku")
async def ws_endpoint(websocket: WebSocket, url: str):
    await websocket.accept()
    
    if "bilibili" in url:
        try:
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
