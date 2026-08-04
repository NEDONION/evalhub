"""持久化 OpenAI-compatible 模型服务商及其加密 API Key。"""

from __future__ import annotations

import ipaddress
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, urlunparse

from evalhub.credentials import CredentialCipher
from evalhub.domain.entities import new_id, utc_now

ProviderKind = Literal["builtin", "custom"]

BUILTIN_PROVIDERS = {
    "deepseek": ("DeepSeek", "https://api.deepseek.com"),
    "siliconflow": ("硅基流动", "https://api.siliconflow.cn/v1"),
    "kimi": ("Kimi", "https://api.moonshot.ai/v1"),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('builtin', 'custom')),
    base_url TEXT NOT NULL,
    encrypted_api_key TEXT,
    key_hint TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class ModelProviderNotFoundError(KeyError):
    """表示调用方引用了不存在的自定义模型服务商。"""


class ModelProviderCredentialError(RuntimeError):
    """表示模型服务商尚未保存可用于请求的 API Key。"""


@dataclass(frozen=True)
class ModelProvider:
    """表示可安全返回给浏览器的脱敏模型服务商配置。"""

    id: str
    name: str
    kind: ProviderKind
    base_url: str
    key_configured: bool
    key_hint: str | None
    created_at: datetime | None
    updated_at: datetime | None


def normalize_provider_base_url(value: str) -> str:
    """校验并规范化模型服务商 API 根地址。

    Args:
        value: 用户填写的远程 HTTPS 或本机回环 HTTP 地址。

    Returns:
        去除首尾空白和末尾斜杠、规范化协议与主机名的根地址。

    Raises:
        ValueError: 地址缺少主机、协议不安全或夹带凭据等禁止部分。
    """
    raw_value = value.strip()
    try:
        parsed = urlparse(raw_value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Base URL is invalid") from exc

    # 服务地址只承担固定 API 根路径，拒绝会改变认证或请求语义的附加部分。
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("Base URL must be an absolute HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Base URL must not contain credentials")
    if parsed.params or parsed.query or parsed.fragment:
        raise ValueError("Base URL must not contain parameters, query, or fragment")

    # 明文 HTTP 仅允许无需跨网络传输凭据的回环地址。
    normalized_host = hostname.lower()
    if scheme == "http" and not _is_loopback_host(normalized_host):
        raise ValueError("Base URL must use HTTPS unless the host is loopback")
    netloc_host = f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    netloc = f"{netloc_host}:{port}" if port is not None else netloc_host

    # 保留厂商要求的版本路径，但统一移除尾斜杠以安全拼接固定端点。
    path = parsed.path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", "", ""))


def _is_loopback_host(hostname: str) -> bool:
    """判断主机名是否明确指向本机回环接口。

    Args:
        hostname: 已转换为小写且不含 IPv6 方括号的主机名。

    Returns:
        主机为 ``localhost`` 或回环 IP 时返回 ``True``。
    """
    if hostname == "localhost":
        return True
    try:
        # 只接受字面量回环 IP，不做 DNS 解析，避免远程主机伪装成本机地址。
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


class ModelProviderRepository:
    """用独立 SQLite 文件保存服务商覆盖配置和 Fernet 密文。"""

    def __init__(self, database_path: Path, cipher: CredentialCipher) -> None:
        """创建仓储并确保服务商表存在。

        Args:
            database_path: 独立服务商 SQLite 文件位置。
            cipher: 负责 API Key 认证加密和解密的凭据组件。
        """
        self.database_path = database_path
        self.cipher = cipher
        # 数据库目录由仓储统一创建，使首次本地启动无需额外初始化命令。
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(_SCHEMA)

    def _connection(self) -> sqlite3.Connection:
        """创建带行对象、WAL 和繁忙等待设置的短生命周期连接。

        Returns:
            可作为上下文管理器提交或回滚事务的 SQLite 连接。
        """
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        # Web 请求和评测 Worker 可能并发读取，WAL 可避免读操作阻塞短写事务。
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def list(self) -> list[ModelProvider]:
        """返回固定顺序的内置预设和按创建时间排列的自定义服务商。

        Returns:
            不包含密文或明文 API Key 的服务商列表。
        """
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM model_providers ORDER BY created_at, id"
            ).fetchall()
        by_id = {str(row["id"]): row for row in rows}

        # 内置记录与默认值按稳定常量顺序合并，删除覆盖后预设会自然重新出现。
        providers = [
            self._builtin_provider(item_id, by_id.get(item_id))
            for item_id in BUILTIN_PROVIDERS
        ]
        providers.extend(
            self._provider_from_row(row)
            for row in rows
            if str(row["id"]) not in BUILTIN_PROVIDERS
        )
        return providers

    def get(self, provider_id: str) -> ModelProvider:
        """按标识读取一个脱敏服务商配置。

        Args:
            provider_id: 内置稳定标识或自定义生成标识。

        Returns:
            合并默认值后的公开服务商对象。

        Raises:
            ModelProviderNotFoundError: 自定义标识不存在。
        """
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM model_providers WHERE id = ?", (provider_id,)
            ).fetchone()
        if provider_id in BUILTIN_PROVIDERS:
            return self._builtin_provider(provider_id, row)
        if row is None:
            raise ModelProviderNotFoundError(f"model provider not found: {provider_id}")
        return self._provider_from_row(row)

    def save(
        self,
        provider_id: str | None,
        *,
        name: str,
        base_url: str,
        api_key: str | None = None,
    ) -> ModelProvider:
        """创建自定义服务商或更新已有服务商覆盖配置。

        Args:
            provider_id: ``None`` 表示创建自定义项；其他值必须是内置项或已有项。
            name: 自定义项展示名称；内置项始终使用固定名称。
            base_url: 通过安全规则校验的 API 根地址。
            api_key: 新凭据；省略或留空表示保留已有密文。

        Returns:
            保存后的脱敏服务商对象。

        Raises:
            ModelProviderNotFoundError: 请求更新的自定义项不存在。
            ValueError: 自定义名称或服务地址无效。
        """
        identifier = provider_id or new_id("provider")
        normalized_url = normalize_provider_base_url(base_url)
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM model_providers WHERE id = ?", (identifier,)
            ).fetchone()
            kind, normalized_name = self._save_identity(identifier, existing, name)

            # 空密码对应 Web 密码框未填写，必须沿用旧密文而不是意外清除凭据。
            encrypted_key = existing["encrypted_api_key"] if existing is not None else None
            key_hint = existing["key_hint"] if existing is not None else None
            normalized_key = api_key.strip() if api_key else ""
            if normalized_key:
                encrypted_key = self.cipher.encrypt(normalized_key)
                key_hint = normalized_key[-4:]
            now = utc_now().isoformat()
            created_at = str(existing["created_at"]) if existing is not None else now

            # 单个 UPSERT 同时覆盖首次保存和后续编辑，避免分离路径产生字段漂移。
            connection.execute(
                """
                INSERT INTO model_providers (
                    id, name, kind, base_url, encrypted_api_key, key_hint, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    base_url = excluded.base_url,
                    encrypted_api_key = excluded.encrypted_api_key,
                    key_hint = excluded.key_hint,
                    updated_at = excluded.updated_at
                """,
                (
                    identifier,
                    normalized_name,
                    kind,
                    normalized_url,
                    encrypted_key,
                    key_hint,
                    created_at,
                    now,
                ),
            )
        return self.get(identifier)

    def _save_identity(
        self,
        provider_id: str,
        existing: sqlite3.Row | None,
        name: str,
    ) -> tuple[ProviderKind, str]:
        """解析保存操作对应的服务商类型和不可伪造的展示名称。

        Args:
            provider_id: 即将写入的稳定或生成标识。
            existing: 当前数据库记录；首次保存时为空。
            name: 调用方提交的展示名称。

        Returns:
            数据库允许写入的类型与规范化名称。

        Raises:
            ModelProviderNotFoundError: 非内置标识没有已有记录。
            ValueError: 自定义名称为空。
        """
        if provider_id in BUILTIN_PROVIDERS:
            return "builtin", BUILTIN_PROVIDERS[provider_id][0]
        if existing is None and not provider_id.startswith("provider_"):
            raise ModelProviderNotFoundError(f"model provider not found: {provider_id}")
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("provider name is required")
        return "custom", normalized_name

    def delete(self, provider_id: str) -> None:
        """删除自定义项，或删除内置项覆盖以恢复默认配置。

        Args:
            provider_id: 要删除或重置的服务商标识。

        Raises:
            ModelProviderNotFoundError: 自定义标识不存在。
        """
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM model_providers WHERE id = ?", (provider_id,))
        # 内置项即使没有覆盖也允许幂等重置，自定义项则需要明确报告不存在。
        if cursor.rowcount == 0 and provider_id not in BUILTIN_PROVIDERS:
            raise ModelProviderNotFoundError(f"model provider not found: {provider_id}")

    def resolve_api_key(self, provider_id: str) -> str:
        """仅为模型调用解析一个服务商的明文 API Key。

        Args:
            provider_id: 已配置凭据的服务商标识。

        Returns:
            仅在当前调用栈短暂存在的 API Key 明文。

        Raises:
            ModelProviderNotFoundError: 自定义标识不存在。
            ModelProviderCredentialError: 服务商尚未配置 API Key。
        """
        with self._connection() as connection:
            row = connection.execute(
                "SELECT encrypted_api_key FROM model_providers WHERE id = ?", (provider_id,)
            ).fetchone()
        if row is None and provider_id not in BUILTIN_PROVIDERS:
            raise ModelProviderNotFoundError(f"model provider not found: {provider_id}")
        encrypted_key = row["encrypted_api_key"] if row is not None else None
        if not encrypted_key:
            raise ModelProviderCredentialError(f"model provider {provider_id} has no API Key")
        return self.cipher.decrypt(str(encrypted_key))

    def _builtin_provider(self, provider_id: str, row: sqlite3.Row | None) -> ModelProvider:
        """把内置默认值与可选数据库覆盖合并为公开对象。

        Args:
            provider_id: 内置服务商稳定标识。
            row: 覆盖记录；尚未保存时为空。

        Returns:
            名称固定且不暴露密文的服务商对象。
        """
        name, default_url = BUILTIN_PROVIDERS[provider_id]
        if row is None:
            return ModelProvider(provider_id, name, "builtin", default_url, False, None, None, None)
        # 内置名称和类型来自代码常量，避免旧数据库或输入篡改产品预设身份。
        return ModelProvider(
            id=provider_id,
            name=name,
            kind="builtin",
            base_url=str(row["base_url"]),
            key_configured=bool(row["encrypted_api_key"]),
            key_hint=str(row["key_hint"]) if row["key_hint"] is not None else None,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def _provider_from_row(self, row: sqlite3.Row) -> ModelProvider:
        """把自定义数据库行转换成不含凭据字段的公开对象。

        Args:
            row: 查询得到的服务商完整数据库行。

        Returns:
            仅包含公开配置和凭据状态的不可变对象。
        """
        return ModelProvider(
            id=str(row["id"]),
            name=str(row["name"]),
            kind="custom",
            base_url=str(row["base_url"]),
            key_configured=bool(row["encrypted_api_key"]),
            key_hint=str(row["key_hint"]) if row["key_hint"] is not None else None,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )


def default_model_provider_repository() -> ModelProviderRepository:
    """构造使用项目默认运行时目录的模型服务商仓储。

    Returns:
        指向 ``.runtime/model_providers.sqlite3`` 的服务商仓储。
    """
    # 从包位置推导项目根目录，避免启动命令的当前目录改变密钥和数据库位置。
    runtime_dir = Path(__file__).resolve().parents[2] / ".runtime"
    cipher = CredentialCipher.from_runtime(runtime_dir)
    return ModelProviderRepository(runtime_dir / "model_providers.sqlite3", cipher)
