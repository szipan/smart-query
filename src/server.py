"""本地开发用 HTTP 服务器 — 把已有的 L2→L5 pipeline 暴露为 REST API。

生产环境走 API Gateway + Lambda；本文件仅供本地前端联调与演示。
用法：  python src/server.py [--port 8000]
"""

from __future__ import annotations

import argparse
import os
import sys

# 确保 src/ 在 import 路径
sys.path.insert(0, os.path.dirname(__file__))

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from adapters.settings import AwsSettings
from api_gateway.handler import GatewayRequest
from query_engine.observer import VerboseEngineObserver
from wiring import build_gateway

app = FastAPI(title="Smart Query Dev Server", version="1.0.0")

# CORS — 允许前端 dev server 跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局 gateway 实例（启动时组装一次）
_gateway = None
_mock_mode = False


def _get_gateway():
    global _gateway, _mock_mode
    if _gateway is None:
        settings = AwsSettings.from_env()
        if not settings.opensearch_endpoint:
            print("[server] ⚠ 未设置 SMARTQUERY_OPENSEARCH_ENDPOINT，启用 mock 模式")
            _mock_mode = True
            return None
        print(f"[server] 组装 pipeline: region={settings.region}, llm={settings.llm_model_id}")
        try:
            _gateway = build_gateway(settings, observer=VerboseEngineObserver())
            print("[server] pipeline 就绪")
        except Exception as e:
            print(f"[server] ⚠ pipeline 构建失败 ({e})，启用 mock 模式")
            _mock_mode = True
            return None
    return _gateway


@app.post("/v1/query")
async def query(request: Request):
    """接收前端请求，走 L2→L3→L4→L5 pipeline，返回结果。"""
    body = await request.json()

    # Mock 模式 — 云资源不可用时返回演示数据
    if _mock_mode:
        return JSONResponse(content=_mock_response(body.get("question", "")))

    # 从 header 提取身份（本地开发简化：默认 user_id=1001）
    user_id = request.headers.get("X-User-Id", "1001")
    request_id = request.headers.get("X-Request-Id", None)

    gw = _get_gateway()
    gw_req = GatewayRequest(
        body=body,
        authorizer_claims={"sub": user_id},
        request_id=request_id,
    )

    import traceback
    try:
        resp = gw.handle(gw_req)
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(content={"answer": "", "sql": None, "data": None, "columns": None,
                                     "cached": False, "error": str(e)}, status_code=200)

    # 适配前端期望的字段格式
    result = resp.body or {}
    frontend_resp = _adapt_response(result)
    # 始终返回 200 给前端（错误信息放在 error 字段），避免前端 axios 抛异常
    return JSONResponse(content=frontend_resp, status_code=200)


def _adapt_response(result: dict) -> dict:
    """把 gateway 响应适配为前端期望的结构。"""
    # Gateway 正常响应: {"request_id": ..., "status": "ok", "data": {...}}
    data = result.get("data", {})
    if isinstance(data, dict):
        return {
            "answer": data.get("answer", data.get("summary", "查询完成")),
            "sql": data.get("safe_sql") or data.get("sql"),
            "data": data.get("rows"),
            "columns": data.get("columns"),
            "cached": data.get("sql_source") == "cache_hit",
            "error": result.get("error", {}).get("message") if "error" in result else None,
        }
    # 错误响应
    if "error" in result:
        return {
            "answer": "",
            "sql": None,
            "data": None,
            "columns": None,
            "cached": False,
            "error": result["error"].get("message", "未知错误"),
        }
    return {"answer": str(data), "sql": None, "data": None, "columns": None, "cached": False, "error": None}


@app.get("/health")
async def health():
    return {"status": "ok", "mode": "mock" if _mock_mode else "live"}


# ─── Mock 响应 ────────────────────────────────────────────────────────────────

_MOCK_DATA = {
    "本月各电站发电量": {
        "sql": "SELECT ps_id, p83022 AS month_power FROM fact_power_station_kpi_month WHERE date_id = 20260701",
        "rows": [
            {"ps_id": 1001, "month_power": 12580.5},
            {"ps_id": 1002, "month_power": 9832.1},
            {"ps_id": 1003, "month_power": 15420.8},
            {"ps_id": 1004, "month_power": 7650.3},
        ],
        "columns": ["ps_id", "month_power"],
    },
    "今年各电站总发电量排名": {
        "sql": "SELECT ps_id, p83022 AS year_power FROM fact_power_station_kpi_year WHERE date_id = 2026 ORDER BY year_power DESC",
        "rows": [
            {"ps_id": 1003, "year_power": 98520.0},
            {"ps_id": 1001, "year_power": 85430.2},
            {"ps_id": 1002, "year_power": 72100.5},
            {"ps_id": 1004, "year_power": 54300.1},
        ],
        "columns": ["ps_id", "year_power"],
    },
    "查询所有电站的装机容量": {
        "sql": "SELECT ps_id, ps_name, install_capacity FROM power_station",
        "rows": [
            {"ps_id": 1001, "ps_name": "阳光家庭电站A", "install_capacity": 25.5},
            {"ps_id": 1002, "ps_name": "屋顶光伏B", "install_capacity": 18.2},
            {"ps_id": 1003, "ps_name": "工商业电站C", "install_capacity": 120.0},
            {"ps_id": 1004, "ps_name": "户用电站D", "install_capacity": 12.8},
        ],
        "columns": ["ps_id", "ps_name", "install_capacity"],
    },
}


def _mock_response(question: str) -> dict:
    """根据问题关键词匹配 mock 数据，找不到则返回通用 mock。"""
    for key, val in _MOCK_DATA.items():
        if key in question or any(w in question for w in key.split("各")):
            return {
                "answer": f"已为您查询：{key}",
                "sql": val["sql"],
                "data": val["rows"],
                "columns": val["columns"],
                "cached": False,
                "error": None,
            }
    # 通用 fallback
    return {
        "answer": f"收到您的问题：「{question}」。当前为 Mock 模式（云资源未就绪），已返回示例数据。",
        "sql": f"-- Mock SQL for: {question}\nSELECT * FROM demo_table LIMIT 5",
        "data": [
            {"id": 1, "name": "示例数据A", "value": 100},
            {"id": 2, "name": "示例数据B", "value": 200},
            {"id": 3, "name": "示例数据C", "value": 300},
        ],
        "columns": ["id", "name", "value"],
        "cached": False,
        "error": None,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    p.add_argument("--host", default="0.0.0.0")
    args = p.parse_args()

    # 预热 pipeline（启动时构建，不要等第一个请求）
    _get_gateway()

    print(f"[server] 启动 http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
