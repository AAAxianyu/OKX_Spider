import os
import json
import datetime
from openai import OpenAI

def load_config(config_path="config.json"):
    """加载配置文件"""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_market_data(file_path):
    """读取爬取的币市数据"""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def analyze_with_deepseek(api_key, model, market_data):
    """调用 DeepSeek API 分析市场数据"""
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

    # 构造输入提示词
    prompt = f"""
你是一名专业的加密货币分析师。
以下是来自 OKX 的市场数据：
{json.dumps(market_data, indent=2)}

请你分析：
1. 未来走势（上涨/下跌可能性及区间）。
2. 可能的入场时机（短线/中线/长线）。
3. 风险提示。

请输出简洁明确的分析建议,并直接总结是否适合长线，长线何时进场。
"""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "你是专业的加密货币市场分析师。"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    )
    return response.choices[0].message.content

def save_result(result_dir, inst_id, analysis):
    """保存分析结果到文件"""
    os.makedirs(result_dir, exist_ok=True)
    filename = f"analysis_{inst_id}_{datetime.datetime.utcnow().strftime('%Y-%m-%d')}.txt"
    file_path = os.path.join(result_dir, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(analysis)

    return file_path

def main():
    config = load_config()
    api_key = config["api_key"]
    data_file = config["data_file"]
    model = config.get("model", "deepseek-chat")
    result_dir = config.get("result_dir", "results")

    # 读取数据
    market_data = load_market_data(data_file)
    inst_id = market_data.get("instId", "Unknown")

    # 调用 DeepSeek
    analysis = analyze_with_deepseek(api_key, model, market_data)

    # 保存结果
    result_file = save_result(result_dir, inst_id, analysis)

    print(f"\n✅ 分析完成，结果已保存到: {result_file}\n")
    print("📊 模型分析结果:\n")
    print(analysis)

if __name__ == "__main__":
    main()
