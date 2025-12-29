# 🚀 Ink-Nado 高频交易套件

> **Nado Protocol on Ink Chain** - 专业版永续合约交易机器人

---

## 🔥 核心特性

本项目为 **Nado Protocol (Ink Chain)** 提供专业的高频交易支持：

- **Web UI 控制面板**: 实时监控账户净值、保证金健康度、交易日志
- **Brute Mode (暴力模式)**: IOC 高频交易策略，快速积累交易量
- **Maker Mode (做市模式)**: 智能价格漂移检测，减少无效订单刷新
- **WebSocket 实时数据**: 经过身份验证的 StarkEx 签名连接，毫秒级延迟
- **紧急风控**: 一键全仓平仓 (`/close_all`)、一键撤单 (`/cancel_all`)

---

## 🛠️ 快速启动

### 1. 安装依赖

```bash
# Python 版本要求: 3.10 - 3.12
pip install -r requirements.txt
```

### 2. 配置环境变量

在项目根目录创建 `.env` 文件，参考 `env_example.txt`：

```bash
# Nado/Ink 专用配置
NADO_WALLET_PRIVATE_KEY=0x...   # 钱包私钥
NADO_SUBACCOUNT_NAME=default    # 子账户名称 (默认: default)
```

### 3. 启动后端 API

```bash
python -m uvicorn api_server:app --host 0.0.0.0 --port 8000
```

### 4. 启动前端 (可选)

```bash
cd web-ui && npm install && npm run dev
```

访问 `http://localhost:3000` 打开控制面板。

---

## 📊 API 端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/start` | POST | 启动交易策略 |
| `/stop` | POST | 停止交易策略 |
| `/stats` | GET | 获取实时 PnL、交易量、持仓 |
| `/close_all` | POST | 紧急全仓平仓 |
| `/cancel_all` | POST | 撤销所有挂单 |

---

## 📖 策略模式

### Maker Mode (做市模式)
在最优买卖价附近挂限价单，等待成交后平仓获利。适合低波动市场。

### Brute Mode (暴力模式)
使用 IOC 订单快速开平仓循环，适合快速积累交易量。

---

## ⚙️ 配置参数

通过 Web UI 或 API 配置以下参数：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `ticker` | 交易对 (ETH, BTC, SOL) | ETH |
| `quantity` | 每笔订单数量 | 0.05 |
| `spread` | 价格偏移比例 | 0.0005 |
| `boost_mode` | 是否启用暴力模式 | false |
| `max_exposure` | 最大敞口 (USD) | 200 |

---

## 🛡️ 风险控制

- **最大敞口限制**: 自动限制总持仓价值
- **紧急平仓**: 一键关闭所有持仓
- **实时监控**: WebSocket 推送账户状态

⚠️ **警告**: 加密货币交易涉及重大风险，可能导致重大财务损失。使用风险自负。

---

## 📁 项目结构

```
ink-nado/
├── api_server.py       # FastAPI 后端服务
├── hft_bot.py          # 高频交易核心引擎
├── pnl_tracker.py      # PnL 追踪器
├── exchanges/
│   ├── nado.py         # Nado 交易所客户端
│   ├── base.py         # 基类定义
│   └── factory.py      # 交易所工厂
├── web-ui/             # Next.js 前端
└── vendor/
    └── edgex-python-sdk/  # StarkEx 签名依赖
```

---

## 📝 许可证

本项目采用非商业许可证 - 详情请参阅 [LICENSE](LICENSE) 文件。

**重要提醒**: 本软件仅供个人学习和研究使用，严禁用于任何商业用途。
