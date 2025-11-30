from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import streamlink
import uvicorn

app = FastAPI()

# --- 允许跨域 (CORS) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "Simple Live 后端服务运行中..."}

# --- 核心功能 1：获取直播流 (支持多画质) ---
@app.get("/api/play")
def get_stream(url: str):
    """
    输入：直播间链接
    输出：所有可用画质的列表 + 默认画质
    """
    try:
        # 1. 特殊处理：如果是测试用的 .m3u8，直接透传
        if ".m3u8" in url and "http" in url:
            return {
                "status": "success",
                "default_quality": "default",
                "qualities": {"default": url}
            }

        # 2. 使用 Streamlink 尝试提取流
        streams = streamlink.streams(url)
        if not streams:
            return {"status": "error", "message": "未找到直播流 (可能未开播)"}

        # 3. 提取所有可用的画质
        quality_map = {}
        for quality, stream_obj in streams.items():
            try:
                # 获取流的真实 URL
                if hasattr(stream_obj, 'url'):
                    quality_map[quality] = stream_obj.url
                # 针对某些 HLS 流，可能需要用 to_url()
                elif hasattr(stream_obj, 'to_url'):
                    quality_map[quality] = stream_obj.to_url()
            except:
                continue

        if not quality_map:
             return {"status": "error", "message": "解析成功但无法获取地址"}

        # 4. 找出默认画质 (优先 best)
        # 逻辑：如果有 best 就用 best，否则取第一个
        default_quality = 'best' if 'best' in quality_map else list(quality_map.keys())[0]
        
        return {
            "status": "success", 
            "default_quality": default_quality,
            "qualities": quality_map
        }

    except Exception as e:
        print(f"Error: {e}")
        return {"status": "error", "message": str(e)}

# --- 核心功能 2：检查开播状态 ---
@app.get("/api/check")
def check_status(url: str):
    try:
        # 简单粗暴：能提取到流就是开播，提取不到就是没播
        streams = streamlink.streams(url)
        if streams:
            return {"is_live": True}
        else:
            return {"is_live": False}
    except:
        return {"is_live": False}

if __name__ == "__main__":
    # 启动服务器，端口 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)