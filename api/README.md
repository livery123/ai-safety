# 公众门户 API（FastAPI）

只读 REST 层，复用 `core/` 与 `services/` 的 MySQL 查询，供 `web/` Next.js 调用。

## 启动

```bash
cd /home/liwr/ai-safety
source venv/bin/activate
uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
```

文档：http://127.0.0.1:8000/docs

## 与 Streamlit 的关系

| 组件 | 用途 |
|------|------|
| `app.py` | 内部管理后台（同步、系统状态） |
| `api/` | 公众门户数据 API |
| `web/` | 公众展示前端 |

## 环境变量

复用根目录 `.env` 中的 MySQL 配置。

可选：

```env
PORTAL_CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```
