# -*- coding: utf-8 -*-
import uvicorn
import json
import asyncio
import re
import aiohttp
import uuid
import time
from typing import Optional
import zlib
import struct # 用于处理小端序/大端序的字节

# ==========================================
# ⚠️ 导入 ProtoBuf 模块 (假设已生成 douyin_pb2.py)
# 🚨 注意：您必须将 douyin_pb2.py 文件也上传到您的 Render 仓库中！
# ==========================================
try:
    # 如果您在本地编译并上传了 douyin_pb2.py，使用这个导入
    import douyin_pb2 
except ImportError:
    # 否则，使用一个占位类来避免 Python 启动时崩溃
    print("❌ douyin_pb2.py 未找到，抖音弹幕功能将无法工作。请编译并上传此文件!")
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
    douyin_pb2 = PlaceholderPB()
    

# ==========================================
# 🔐 配置区域 (请替换为您自己的 SESSDATA)
# ==========================================
SESSDATA = "0d5ceb32%2C1779919308%2Ca276a%2Ab1CjCr1DByEwubcFGNC3jSZC18fEm4MgMO-3b2yE5CSquh_pZ8_jQ8esjl1MaTj_W59QUSVndxRkpSUEE5TjVDOXU0ZkJXamtrUnBlalNhTm5zZ0RBQm5zWXBJTm94SFpkQzU4bmg2Z21fbFJ6Z1RHRVBSSndmckI2WTZlOHY3M096YWhXVlJocVN3IIEC"
from bilibili_api import live, Credential
CREDENTIAL = Credential(sessdata=SESSDATA)

# ==========================================
# ⬇️ 抖音 ProtoBuf 核心逻辑
# ==========================================

# 抖音客户端发送请求的 LogID，必须是唯一的
def get_log_id() -> str:
    return str(uuid.uuid4()).replace('-', '')[0:16]

# 核心编码函数：构造客户端请求帧
def encode_douyin_ws_frame(log_id: str, payload_type: str, payload: bytes) -> bytes:
    """
    构造客户端的 PushFrame 消息体 (ProtoBuf Request).
    
    Args:
        log_id: 用于追踪的唯一ID。
        payload_type: 例如 'WebcastPushFrame'.
        payload: 实际的业务 ProtoBuf 数据 (例如 Request Body 或 Heartbeat).
    
    Returns:
        序列化后的二进制字节。
    """
    
    # 1. 构造 PushFrame 消息
    push_frame = douyin_pb2.Webcast.Im.PushFrame()
    push_frame.SeqID = int(time.time() * 1000)
    push_frame.LogID = int(log_id, 16) if log_id.startswith('0x') else int(log_id, 16)
    push_frame.service = 3 # Service: 3 (Webcast), Method: 4 (PushFrame)
    push_frame.method = 4 
    push_frame.payload_encoding = 'none'
    push_frame.payload_type = payload_type
    push_frame.payload = payload
    
    # 2. 序列化并返回
    return push_frame.SerializeToString()

# 核心解码函数：解析服务器返回的帧
def decode_douyin_ws_frame(data: bytes) -> dict:
    """
    解析服务器返回的 ProtoBuf 帧。
    
    Args:
        data: 原始二进制数据。
        
    Returns:
        包含 messages 列表、log_id 等信息的字典。
    """
    messages = []
    
    # 1. 反序列化外层 PushFrame
    push_frame = douyin_pb2.Webcast.Im.PushFrame()
    try:
        push_frame.ParseFromString(data)
    except Exception as e:
        return {"messages": [], "log_id": "ParseFrameError", "error": f"PushFrame解析失败: {e}"}

    # 2. 检查 Payload 是否被压缩 (payload_encoding: gzip/zlib/none)
    payload_data = push_frame.payload
    if push_frame.payload_encoding == 'gzip' or push_frame.payload_encoding == 'zlib':
        try:
            # 尝试解压 (使用 zlib.MAX_WBITS + 16 for gzip)
            payload_data = zlib.decompress(payload_data, 16 + zlib.MAX_WBITS)
        except Exception as e:
            return {"messages": [], "log_id": push_frame.LogID, "error": f"解压失败: {e}"}

    # 3. 解析内层 Response (包含多个 Message)
    if push_frame.payload_type == 'WebcastResponse':
        response = douyin_pb2.Webcast.Im.Response()
        try:
            response.ParseFromString(payload_data)
        except Exception as e:
            return {"messages": [], "log_id": push_frame.LogID, "error": f"Response解析失败: {e}"}
        
        # 4. 遍历所有内嵌消息
        for msg in response.messages:
            # 假设 DanmuMessage 是最常见的，其 method 为 "WebcastChatMessage"
            if msg.method == 'WebcastChatMessage':
                try:
                    chat_message = douyin_pb2.Webcast.Im.ChatMessage()
                    chat_message.ParseFromString(msg.payload)
                    
                    # 提取弹幕内容 (Chat.content)
                    messages.append({
                        "type": "danmaku",
                        "text": chat_message.content,
                        "user": chat_message.user.nickname,
                        "platform": "douyin"
                    })
                except Exception as e:
                    print(f"ChatMsg解析失败: {e}")
                    
            # TODO: 您可以根据需要添加对礼物(GiftMessage)等其他消息的解析逻辑

        return {
            "messages": messages, 
            "log_id": push_frame.LogID, 
            "cursor": response.cursor,
            "internal_ext": response.internal_ext
        }

    return {"messages": [], "log_id": push_frame.LogID, "error": "未知 Payload Type"}

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
# ⬇️ 抖音弹幕代理 (ProtoBuf 集成版)
# ==========================================

# 周期性发送心跳包
async def _douyin_heartbeat_sender(ws: aiohttp.ClientWebSocketResponse):
    heartbeat_payload = douyin_pb2.Webcast.Im.Request()
    heartbeat_payload.live_id = 0 
    
    heartbeat_frame = encode_douyin_ws_frame(
        log_id=get_log_id(),
        payload_type='WebcastHeartbeat',
        payload=heartbeat_payload.SerializeToString()
    )
    
    try:
        while True:
            await asyncio.sleep(10) # 抖音心跳周期通常是 10-20 秒
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
    # 🚨 警告: 这里的 Headers 极有可能需要运行时动态获取和更新，否则连接会失败。
    DOUYIN_WS_BASE = "wss://webcast-ws-web-lf.douyin.com/ws/room/?compress=lz4&version=1.0.0" 
    DOUYIN_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36',
        'Referer': f'https://live.douyin.com/{room_id}',
        'Cookie': 'YOUR_VALID_COOKIE_HERE', # 关键
    }
    
    try:
        async with aiohttp.ClientSession(headers=DOUYIN_HEADERS) as session:
            async with session.ws_connect(DOUYIN_WS_BASE, timeout=15) as ws:
                
                # --- 2. 构造并发送房间认证请求 ---
                auth_request = douyin_pb2.Webcast.Im.Request()
                auth_request.room_id = room_id
                auth_request.device_platform = "web"
                auth_request.aid = 1128 # 模拟浏览器 aid
                # TODO: 填充更多必要的字段，例如 ac, version_code, unique_id, cursor等
                
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
