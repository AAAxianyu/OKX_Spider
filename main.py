import asyncio
import json
import time
import requests
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

# OKX API配置
OKX_REST_URL = "https://www.okx.com"

class EMAAnalyzer:
    """EMA分析器，用于计算EMA和检测金叉死叉信号"""
    
    def __init__(self, ema_short: int = 12, ema_long: int = 26):
        self.ema_short = ema_short
        self.ema_long = ema_long
    
    def calculate_ema(self, prices: List[float], period: int) -> List[float]:
        """计算EMA指标"""
        if len(prices) < period:
            return [None] * len(prices)
        
        ema_values = [None] * len(prices)
        # 第一个EMA值使用SMA
        sma = sum(prices[:period]) / period
        ema_values[period - 1] = sma
        
        # 计算后续EMA值
        multiplier = 2 / (period + 1)
        for i in range(period, len(prices)):
            ema_values[i] = (prices[i] * multiplier) + (ema_values[i - 1] * (1 - multiplier))
        
        return ema_values
    
    def detect_cross_signal(self, ema_short: List[float], ema_long: List[float], 
                          close_prices: List[float]) -> Optional[str]:
        """检测金叉死叉信号"""
        if len(ema_short) < 2 or len(ema_long) < 2 or len(close_prices) < 1:
            return None
        
        # 获取最新的两个EMA值
        current_short = ema_short[-1]
        current_long = ema_long[-1]
        prev_short = ema_short[-2]
        prev_long = ema_long[-2]
        current_close = close_prices[-1]
        
        if None in [current_short, current_long, prev_short, prev_long]:
            return None
        
        # 检测金叉：短期EMA从下方穿越长期EMA
        if prev_short <= prev_long and current_short > current_long:
            if current_close > 0:  # 收盘价为正（上涨）
                return "做多"
        
        # 检测死叉：短期EMA从上方穿越长期EMA
        if prev_short >= prev_long and current_short < current_long:
            if current_close < 0:  # 收盘价为负（下跌）
                return "做空"
        
        return None

class OKXMonitor:
    """OKX市场数据监视器"""
    
    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.analyzer = EMAAnalyzer()
        self.last_4h_analysis_hour = -1
        self.last_1h_analysis_hour = -1
        self.last_15m_analysis_minute = -1
    
    def fetch_candles(self, inst_id: str, bar: str, limit: int = 100) -> List[Dict]:
        """获取K线数据"""
        url = f"{OKX_REST_URL}/api/v5/market/candles"
        params = {"instId": inst_id, "bar": bar, "limit": limit}
        
        try:
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            if data.get("code") == "0":
                return data.get("data", [])
            else:
                print(f"获取{inst_id} {bar}数据失败: {data.get('msg', '未知错误')}")
                return []
        except Exception as e:
            print(f"获取{inst_id} {bar}数据异常: {e}")
            return []
    
    def fetch_4h_candles(self, inst_id: str, limit: int = 100) -> List[Dict]:
        """获取4小时K线数据"""
        return self.fetch_candles(inst_id, "4H", limit)
    
    def fetch_1h_candles(self, inst_id: str, limit: int = 100) -> List[Dict]:
        """获取1小时K线数据"""
        return self.fetch_candles(inst_id, "1H", limit)
    
    def fetch_15m_candles(self, inst_id: str, limit: int = 100) -> List[Dict]:
        """获取15分钟K线数据"""
        return self.fetch_candles(inst_id, "15m", limit)
    
    def process_candles(self, candles: List[Dict]) -> Dict[str, List[float]]:
        """处理K线数据，提取价格信息"""
        if not candles:
            return {"close": [], "high": [], "low": [], "open": []}
        
        # OKX返回的K线数据是倒序的，需要反转
        candles = list(reversed(candles))
        
        close_prices = [float(candle[4]) for candle in candles]  # 收盘价
        high_prices = [float(candle[2]) for candle in candles]   # 最高价
        low_prices = [float(candle[3]) for candle in candles]    # 最低价
        open_prices = [float(candle[1]) for candle in candles]   # 开盘价
        
        return {
            "close": close_prices,
            "high": high_prices,
            "low": low_prices,
            "open": open_prices
        }
    
    def calculate_price_change(self, close_prices: List[float]) -> List[float]:
        """计算价格变化（收盘价相对于前一根K线的变化）"""
        if len(close_prices) < 2:
            return [0.0]
        
        changes = [0.0]  # 第一根K线变化为0
        for i in range(1, len(close_prices)):
            change = close_prices[i] - close_prices[i-1]
            changes.append(change)
        
        return changes
    
    def should_analyze_4h_now(self) -> bool:
        """检查是否应该在当前时间进行4小时分析"""
        now = datetime.now(timezone.utc)
        current_hour = now.hour
        
        # 检查是否在指定的小时（0,4,8,12,16,20）
        target_hours = [0, 4, 8, 12, 16, 20]
        if current_hour not in target_hours:
            return False
        
        # 确保每个小时只分析一次
        if current_hour == self.last_4h_analysis_hour:
            return False
        
        # 检查是否在4小时K线的开始时间（0,4,8,12,16,20点的0-5分钟内）
        current_minute = now.minute
        if current_minute > 5:  # 只在前5分钟内分析
            return False
        
        return True
    
    def should_analyze_1h_now(self) -> bool:
        """检查是否应该在当前时间进行1小时分析"""
        now = datetime.now(timezone.utc)
        current_hour = now.hour
        current_minute = now.minute
        
        # 确保每个小时只分析一次
        if current_hour == self.last_1h_analysis_hour:
            return False
        
        # 检查是否在1小时K线的开始时间（每小时的0-5分钟内）
        if current_minute > 5:  # 只在前5分钟内分析
            return False
        
        return True
    
    def should_analyze_15m_now(self) -> bool:
        """检查是否应该在当前时间进行15分钟分析"""
        now = datetime.now(timezone.utc)
        current_minute = now.minute
        
        # 确保每15分钟只分析一次
        if current_minute == self.last_15m_analysis_minute:
            return False
        
        # 检查是否在15分钟K线的开始时间（每15分钟的0-2分钟内）
        minute_in_15m_cycle = current_minute % 15
        if minute_in_15m_cycle > 2:  # 只在前2分钟内分析
            return False
        
        return True
    
    def analyze_symbol(self, symbol: str, timeframe: str) -> Optional[str]:
        """分析单个交易对"""
        print(f"正在分析 {symbol} ({timeframe})...")
        
        # 根据时间框架获取K线数据
        if timeframe == "4H":
            candles = self.fetch_4h_candles(symbol, 50)  # 获取50根4小时K线
        elif timeframe == "1H":
            candles = self.fetch_1h_candles(symbol, 50)  # 获取50根1小时K线
        elif timeframe == "15m":
            candles = self.fetch_15m_candles(symbol, 50)  # 获取50根15分钟K线
        else:
            print(f"不支持的时间框架: {timeframe}")
            return None
        
        if not candles:
            print(f"无法获取 {symbol} 的{timeframe}K线数据")
            return None
        
        # 处理K线数据
        price_data = self.process_candles(candles)
        close_prices = price_data["close"]
        
        if len(close_prices) < 30:  # 确保有足够的数据计算EMA
            print(f"{symbol} {timeframe} 数据不足，跳过分析")
            return None
        
        # 计算EMA
        ema_short = self.analyzer.calculate_ema(close_prices, self.analyzer.ema_short)
        ema_long = self.analyzer.calculate_ema(close_prices, self.analyzer.ema_long)
        
        # 计算价格变化
        price_changes = self.calculate_price_change(close_prices)
        
        # 检测金叉死叉信号
        signal = self.analyzer.detect_cross_signal(ema_short, ema_long, price_changes)
        
        if signal:
            print(f"🎯 {symbol} ({timeframe}) {signal}")
            return f"{symbol} ({timeframe}) {signal}"
        
        return None
    
    async def run_monitor(self):
        """运行监视器"""
        print("🚀 OKX EMA金叉死叉监视器启动")
        print("📊 监视交易对:", ", ".join(self.symbols))
        print("⏰ 4H分析时间点: 0, 4, 8, 12, 16, 20点（UTC时间）")
        print("⏰ 1H分析时间点: 每小时（UTC时间）")
        print("⏰ 15m分析时间点: 每15分钟（UTC时间）")
        print("📈 策略: EMA12上穿EMA26且收盘为正 -> 做多")
        print("📉 策略: EMA12下穿EMA26且收盘为负 -> 做空")
        print("-" * 60)
        
        while True:
            try:
                current_time = datetime.now(timezone.utc)
                all_signals = []
                analysis_performed = False
                
                # 检查4小时分析
                if self.should_analyze_4h_now():
                    print(f"⏰ 当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                    print("🔍 开始4小时分析...")
                    self.last_4h_analysis_hour = current_time.hour
                    analysis_performed = True
                    
                    signals_4h = []
                    for symbol in self.symbols:
                        signal = self.analyze_symbol(symbol, "4H")
                        if signal:
                            signals_4h.append(signal)
                    
                    if signals_4h:
                        print("🎯 发现4小时交易信号:")
                        for signal in signals_4h:
                            print(f"  ✅ {signal}")
                        all_signals.extend(signals_4h)
                    else:
                        print("❌ 4小时分析无交易信号")
                
                # 检查1小时分析
                if self.should_analyze_1h_now():
                    print(f"⏰ 当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                    print("🔍 开始1小时分析...")
                    self.last_1h_analysis_hour = current_time.hour
                    analysis_performed = True
                    
                    signals_1h = []
                    for symbol in self.symbols:
                        signal = self.analyze_symbol(symbol, "1H")
                        if signal:
                            signals_1h.append(signal)
                    
                    if signals_1h:
                        print("🎯 发现1小时交易信号:")
                        for signal in signals_1h:
                            print(f"  ✅ {signal}")
                        all_signals.extend(signals_1h)
                    else:
                        print("❌ 1小时分析无交易信号")
                
                # 检查15分钟分析
                if self.should_analyze_15m_now():
                    print(f"⏰ 当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
                    print("🔍 开始15分钟分析...")
                    self.last_15m_analysis_minute = current_time.minute
                    analysis_performed = True
                    
                    signals_15m = []
                    for symbol in self.symbols:
                        signal = self.analyze_symbol(symbol, "15m")
                        if signal:
                            signals_15m.append(signal)
                    
                    if signals_15m:
                        print("🎯 发现15分钟交易信号:")
                        for signal in signals_15m:
                            print(f"  ✅ {signal}")
                        all_signals.extend(signals_15m)
                    else:
                        print("❌ 15分钟分析无交易信号")
                
                # 输出所有信号汇总
                if all_signals:
                    print("📊 信号汇总:")
                    for signal in all_signals:
                        print(f"  🎯 {signal}")
                elif analysis_performed:
                    print("❌ 当前无任何交易信号")
                
                # 如果没有任何分析，显示静默等待信息（每10分钟显示一次）
                if not analysis_performed and current_time.minute % 10 == 0:
                    print(f"⏳ 等待分析时间点... ({current_time.strftime('%H:%M')} UTC)")
                
                if analysis_performed:
                    print("-" * 60)
                
                # 每分钟检查一次
                await asyncio.sleep(60)
                
            except KeyboardInterrupt:
                print("\n🛑 监视器已停止")
                break
            except Exception as e:
                print(f"❌ 运行异常: {e}")
                await asyncio.sleep(60)

async def main():
    """主函数"""
    # 监视的交易对
    symbols = [
    "BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT", "ADA-USDT", "AVAX-USDT",
    "DOT-USDT", "TRX-USDT", "LTC-USDT", "BCH-USDT", "ETC-USDT", "SHIB-USDT", "LINK-USDT",
    "TON-USDT", "ICP-USDT", "FIL-USDT", "APT-USDT", "ARB-USDT", "OP-USDT", "SUI-USDT", "INJ-USDT",
    "NEAR-USDT", "STX-USDT", "WLD-USDT", "PEPE-USDT", "UNI-USDT", "LDO-USDT", "GMX-USDT",
    "DYDX-USDT", "IMX-USDT", "ALGO-USDT", "HBAR-USDT", "GRT-USDT", "MANA-USDT"
]

    
    # 创建监视器
    monitor = OKXMonitor(symbols)
    
    # 运行监视器
    await monitor.run_monitor()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 程序已退出")
    except Exception as e:
        print(f"❌ 程序异常: {e}")
