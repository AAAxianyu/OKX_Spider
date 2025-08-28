import asyncio
import json
import os
import time
import traceback
import contextlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from datetime import datetime, timezone
import websockets
import requests


OKX_PUBLIC_WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
OKX_REST_URL = "https://www.okx.com"


# ---------------- REST 部分 ----------------

def fetch_candles(inst_id: str, bar: str = "1m", limit: int = 100):
    url = f"{OKX_REST_URL}/api/v5/market/candles"
    params = {"instId": inst_id, "bar": bar, "limit": limit}
    return requests.get(url, params=params, timeout=10).json()


def fetch_trades(inst_id: str, limit: int = 50):
    url = f"{OKX_REST_URL}/api/v5/market/trades"
    params = {"instId": inst_id, "limit": limit}
    return requests.get(url, params=params, timeout=10).json()


def fetch_orderbook(inst_id: str, sz: int = 50):
    url = f"{OKX_REST_URL}/api/v5/market/books"
    params = {"instId": inst_id, "sz": sz}
    return requests.get(url, params=params, timeout=10).json()


def fetch_ticker(inst_id: str):
    url = f"{OKX_REST_URL}/api/v5/market/ticker"
    params = {"instId": inst_id}
    return requests.get(url, params=params, timeout=10).json()


def save_rest_data(inst_id: str, output_dir="./okx_out/history"):
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    candles = fetch_candles(inst_id, "1m", 100).get("data", [])
    trades = fetch_trades(inst_id, 50).get("data", [])
    orderbook = fetch_orderbook(inst_id, 50).get("data", [])
    ticker = fetch_ticker(inst_id).get("data", [])

    payload = {
        "instId": inst_id,
        "timestamp": ts,
        "source": "REST",
        "data": {
            "candles": candles,
            "trades": trades,
            "orderbook": orderbook[0] if orderbook else {},
            "tickers": ticker[0] if ticker else {}
        }
    }

    path = os.path.join(output_dir, f"{inst_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


# ---------------- WebSocket 部分 ----------------

@dataclass
class Subscription:
    channel: str
    inst_id: str

    def to_arg(self) -> Dict[str, str]:
        return {"channel": self.channel, "instId": self.inst_id}


@dataclass
class WriterConfig:
    output_dir: str
    file_rotate_seconds: int = 60


@dataclass
class ClientConfig:
    url: str = OKX_PUBLIC_WS_URL
    ping_interval_seconds: int = 15
    reconnect_base_delay_seconds: float = 1.0
    reconnect_max_delay_seconds: float = 30.0


class RollingJsonWriter:
    def __init__(self, config: WriterConfig) -> None:
        self.config = config
        os.makedirs(self.config.output_dir, exist_ok=True)

    def _build_filename(self, inst_id: str, channel: str, epoch_ms: int) -> str:
        ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime(epoch_ms / 1000))
        base = f"{inst_id}_{channel}_{ts}.json"
        return os.path.join(self.config.output_dir, base)

    async def flush_buffers(self, buffers: Dict[Tuple[str, str], List[Dict[str, Any]]], now_ms: int) -> None:
        for (inst_id, channel), messages in list(buffers.items()):
            if not messages:
                continue
            path = self._build_filename(inst_id, channel, now_ms)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(messages, f, ensure_ascii=False, indent=2)
            except Exception:
                traceback.print_exc()
            finally:
                buffers[(inst_id, channel)] = []


class OkxPublicWSClient:
    def __init__(self, client_config: ClientConfig, writer: RollingJsonWriter) -> None:
        self.client_config = client_config
        self.writer = writer
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._subscriptions: List[Subscription] = []
        self._buffers: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        self._last_flush_ms: int = 0
        self._stop: bool = False

    def set_subscriptions(self, subscriptions: List[Subscription]) -> None:
        self._subscriptions = subscriptions
        for sub in subscriptions:
            key = (sub.inst_id, sub.channel)
            if key not in self._buffers:
                self._buffers[key] = []

    async def _send(self, payload: Dict[str, Any]) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps(payload, separators=(",", ":")))

    async def _subscribe_all(self) -> None:
        if not self._subscriptions:
            return
        args = [sub.to_arg() for sub in self._subscriptions]
        await self._send({"op": "subscribe", "args": args})

    async def _ping_loop(self) -> None:
        while not self._stop and self._ws is not None:
            try:
                await asyncio.sleep(self.client_config.ping_interval_seconds)
                await self._send({"op": "ping"})
            except Exception:
                break

    async def _flush_loop(self, rotate_seconds: int) -> None:
        rotate_ms = rotate_seconds * 1000
        while not self._stop:
            try:
                await asyncio.sleep(1)
                now_ms = int(time.time() * 1000)
                if self._last_flush_ms == 0:
                    self._last_flush_ms = now_ms
                if now_ms - self._last_flush_ms >= rotate_ms:
                    await self.writer.flush_buffers(self._buffers, now_ms)
                    self._last_flush_ms = now_ms
            except Exception:
                traceback.print_exc()

    async def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return

        if isinstance(msg, dict) and msg.get("event") in {"pong", "subscribe", "error"}:
            return

        arg = msg.get("arg")
        data = msg.get("data")
        action = msg.get("action")
        if not arg or not data:
            return

        channel = arg.get("channel")
        inst_id = arg.get("instId")
        key = (inst_id, channel)

        if key not in self._buffers:
            self._buffers[key] = []

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "instId": inst_id,
            "timestamp": ts,
            "source": "WS",
            "channel": channel,
            "data": data
        }
        if action is not None:
            payload["action"] = action

        self._buffers[key].append(payload)

    async def _connect_once(self) -> None:
        assert self._subscriptions, "未设置订阅频道"
        async with websockets.connect(self.client_config.url, ping_interval=None) as ws:
            self._ws = ws
            await self._subscribe_all()

            ping_task = asyncio.create_task(self._ping_loop())
            try:
                async for message in ws:
                    await self._handle_message(message)
            finally:
                ping_task.cancel()
                with contextlib.suppress(Exception):
                    await ping_task

    async def run_forever(self, rotate_seconds: int) -> None:
        self._stop = False
        flush_task = asyncio.create_task(self._flush_loop(rotate_seconds))
        delay = self.client_config.reconnect_base_delay_seconds
        try:
            while not self._stop:
                try:
                    await self._connect_once()
                    delay = self.client_config.reconnect_base_delay_seconds
                except Exception:
                    traceback.print_exc()
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self.client_config.reconnect_max_delay_seconds)
        finally:
            self._stop = True
            flush_task.cancel()
            with contextlib.suppress(Exception):
                await flush_task


# ---------------- 主程序 ----------------

async def main():
    symbols = ["AVAX-USDT"]
    channels = ["tickers", "trades", "books", "candle1m"]

    # 先用 REST 拉一次全量历史数据
    for symbol in symbols:
        save_rest_data(symbol)

    # 再启动 WebSocket 实时订阅
    subs = [Subscription(channel=c, inst_id=s) for s in symbols for c in channels]
    writer = RollingJsonWriter(WriterConfig(output_dir="./okx_out/realtime", file_rotate_seconds=60))
    client = OkxPublicWSClient(ClientConfig(), writer)
    client.set_subscriptions(subs)

    await client.run_forever(rotate_seconds=writer.config.file_rotate_seconds)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
