"""创建可选 FastAPI 应用并暴露基础健康检查。"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app() -> "FastAPI":
    """构建 EvalHub FastAPI 实例并注册当前可用路由。

    Returns:
        已配置服务名称、版本和健康检查路由的 FastAPI 应用。

    Raises:
        RuntimeError: 当前环境尚未安装项目的 API 可选依赖。
    """
    try:
        # 延迟导入允许核心评测功能在未安装 FastAPI 时继续正常使用。
        from fastapi import FastAPI
    except ImportError as exc:
        # 把底层模块缺失转换为包含可执行安装命令的项目级错误。
        raise RuntimeError('Install API dependencies with: pip install -e ".[api]"') from exc

    # 应用元数据与项目版本一致，便于 OpenAPI 文档和服务探针识别。
    app = FastAPI(title="EvalHub", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        """返回无需外部依赖的轻量服务存活状态。"""
        # 固定响应字段便于容器探针和本地诊断脚本稳定解析。
        return {"status": "ok", "service": "evalhub"}

    return app


# 模块级实例遵循 ASGI 服务器的标准发现约定 ``evalhub.api.main:app``。
app = create_app()
