# -*- coding: utf-8 -*-
import uvicorn
import json
import asyncio
import re
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from bilibili_api import live, Credential
import aiohttp

# ==========================================
# 🔐 配置区域 (请替换为您自己的 SESSDATA)
# ==========================================
SESSDATA = "0d5ceb32%2C1779919308%2Ca276a%2Ab1CjCr1DByEwubcFGNC3jSZC18fEm4MgMO-3b2yE5CSquh_pZ8_jQ8esjl1MaTj_W59QUSVndxRkpSUEE5TjVDOXU0ZkJXamtrUnBlalNhTm5zZ0RBQm5zWXBJTm94SFpkQzU4bmg2Z21fbFJ6Z1RHRVBSSndmckI2WTZlOHY3M096YWhXVlJocVN3IIEC"
CREDENTIAL = Credential(sessdata=SESSDATA)

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
        # Streamlink 使用 SESSDATA 解决 B站的登录限制
        session.set_option("http-headers", {'Cookie': f'SESSDATA={SESSDATA}'})
        streams = session.streams(clean_url)
        if not streams: return {"status": "error", "message": "未找到流"}
        
        quality_map = {}
        for q, s in streams.items():
            try:
                # 尝试获取 Streamlink 解析出的真实 URL
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
        # 尝试获取 streams 列表，如果能获取到则认为开播
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
            # 尝试发送给前端，如果失败则抛出异常，触发 finally 清理
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
            # 使用 asyncio.wait_for 探活前端和 B站连接
            await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            
            # 检查 B站连接是否意外断开
            if connect_task.done() and connect_task.exception():
                print(f"❌ B站任务异常: {connect_task.exception()}")
                break

    except WebSocketDisconnect:
        print("🔌 B站: 前端断开，停止接收弹幕")
    except asyncio.TimeoutError:
         pass # 正常超时，继续循环
    except Exception as e:
        print(f"❌ B站异常中断: {e}")
    finally:
        print("🧹 清理 B站 资源...")
        try:
            connect_task.cancel() # 强制取消 B站 连接任务
            await room.disconnect()
        except: pass

# ==========================================
# ⬇️ 抖音弹幕代理 (云端友好架构)
# ==========================================

async def start_douyin_room(url, websocket: WebSocket):
    # 1. 提取房间 ID (简化提取)
    match = re.search(r'live/([a-zA-Z0-9]+)', url)
    room_id = match.group(1) if match else None
    
    if not room_id:
        await websocket.send_text(json.dumps({"type": "system", "text": "抖音: 无法解析房间ID"}))
        return

    print(f"🚀 [抖音] 正在连接: {room_id}")

    # --- TODO 1: 获取 WebSocket 地址和 Headers ---
    # 这部分代码非常容易失效，需要根据最新的逆向结果来填充。
    # 在云端环境中，你必须通过正常的 HTTP 请求，模拟浏览器，获取到 WebSocket 连接所需的一切参数。
    # 
    # Placeholder values:
    DOUYIN_WS_URL = "wss://webcast-ws-web-lf.douyin.com/ws/room/?compress=lz4&version=1.0.0" 
    DOUYIN_HEADERS = {
        'Cookie': 'YOUR_DOUYIN_COOKIE_HERE', # 替换成您的 Cookie
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.75 Safari/537.36',
        'Referer': f'https://live.douyin.com/{room_id}',
        'sec-websocket-protocol': 'websocket-protocol' # 必须有
    }
    # ---------------------------------------------
    
    try:
        # 使用 aiohttp 建立 WebSocket 连接
        async with aiohttp.ClientSession(headers=DOUYIN_HEADERS) as session:
            async with session.ws_connect(DOUYIN_WS_URL, timeout=10) as ws:
                
                await websocket.send_text(json.dumps({"type": "system", "text": "抖音: 连接成功，正在等待数据流..."}))
                
                # --- TODO 2: 发送房间认证或握手消息 ---
                # 抖音连接后需要发送一个 ProtoBuf 消息来认证房间。
                # 示例: await ws.send_bytes(auth_protobuf_message)
                # -------------------------------------

                # 5. 循环接收消息
                while True:
                    # 使用 asyncio.wait([WS接收], [前端探活])
                    
                    # 尝试从抖音WS接收数据 (1秒超时)
                    douyin_msg_task = asyncio.create_task(ws.receive())
                    # 尝试从前端接收数据 (用于探活, 1秒超时)
                    frontend_probe_task = asyncio.create_task(websocket.receive_text())
                    
                    done, pending = await asyncio.wait(
                        [douyin_msg_task, frontend_probe_task],
                        timeout=1.0,
                        return_when=asyncio.FIRST_COMPLETED
                    )

                    for task in pending:
                        task.cancel()
                    
                    if frontend_probe_task in done:
                         # 如果前端发来消息，说明前端活跃
                         try: await frontend_probe_task
                         except: raise WebSocketDisconnect() # 实际是前端断开
                    
                    if douyin_msg_task in done:
                        msg = await douyin_msg_task
                        
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            # --- TODO 3: ProtoBuf 解码和解析 ---
                            # msg.data 是二进制 ProtoBuf 数据，需要解析出弹幕内容。
                            # 
                            # 假设解析后得到: content, user_name
                            simulated_text = "[抖音 ProtoBuf 待解析]"
                            
                            await websocket.send_text(json.dumps({
                                "type": "danmaku",
                                "text": simulated_text,
                                "user": "抖音用户",
                                "platform": "douyin"
                            }))
                            # -------------------------------------

                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            print("❌ 抖音 WebSocket 已关闭")
                            raise WebSocketDisconnect()

    except aiohttp.ClientConnectorError as e:
        await websocket.send_text(json.dumps({"type": "system", "text": f"抖音: 连接失败 (网络/地址错误)"}))
        print(f"❌ 抖音连接异常: {e}")
    except WebSocketDisconnect:
        print("🔌 前端断开，停止接收抖音弹幕")
    except Exception as e:
        await websocket.send_text(json.dumps({"type": "system", "text": f"抖音: 发生未处理的错误: {e}"}))
        print(f"❌ 抖音异常中断: {e}")
    finally:
        print("🧹 清理抖音资源...")
        # aiohttp 的 session 和 ws 离开 async with 块后会自动清理


# ==========================================
# 🌐 WebSocket 路由
# ==========================================

@app.websocket("/ws/danmaku")
async def ws_endpoint(websocket: WebSocket, url: str):
    await websocket.accept()
    
    if "bilibili" in url:
        await start_bilibili_room(int(url.split('?')[0].split('/')[-1]), websocket)
    
    elif "douyin" in url:
        await start_douyin_room(url, websocket)

    else:
        await websocket.send_text(json.dumps({"type": "system", "text": "平台未支持弹幕功能"}))
        try:
            while True: await websocket.receive_text()
        except: pass

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

