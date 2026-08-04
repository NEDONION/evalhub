"""验证模型服务商预设、加密凭据和 SQLite 持久化边界。"""

import sqlite3
from pathlib import Path

import pytest

from evalhub.credentials import CredentialCipher
from evalhub.model_providers import (
    ModelProviderCredentialError,
    ModelProviderNotFoundError,
    ModelProviderRepository,
    normalize_provider_base_url,
)


@pytest.fixture
def repository(tmp_path: Path) -> ModelProviderRepository:
    """创建使用临时主密钥和数据库的隔离服务商仓储。

    Args:
        tmp_path: pytest 为当前用例创建的临时目录。

    Returns:
        不会读取真实运行时凭据的服务商仓储。
    """
    # 密钥文件与数据库均放入临时目录，保证测试不会污染项目运行时状态。
    cipher = CredentialCipher.from_runtime(tmp_path, env={})
    return ModelProviderRepository(tmp_path / "providers.sqlite3", cipher)


def test_repository_lists_presets_without_exposing_keys(
    repository: ModelProviderRepository,
) -> None:
    """空数据库应展示固定顺序的内置预设且公开对象不含密文字段。"""
    providers = repository.list()

    # 内置预设无需预写数据库，首次打开设置页即可直接选择。
    assert [item.id for item in providers] == ["deepseek", "siliconflow", "kimi"]
    assert [item.kind for item in providers] == ["builtin", "builtin", "builtin"]
    assert all(item.key_configured is False for item in providers)
    assert all(not hasattr(item, "encrypted_api_key") for item in providers)


def test_empty_key_update_preserves_encrypted_credential(
    repository: ModelProviderRepository,
) -> None:
    """编辑地址时留空密码字段应保留已有凭据并只公开末四位提示。"""
    repository.save(
        "deepseek",
        name="被忽略的内置名称",
        base_url="https://api.deepseek.com/",
        api_key="sk-first",
    )
    repository.save(
        "deepseek",
        name="DeepSeek",
        base_url="https://gateway.example.com/v1/",
        api_key="",
    )

    # API Key 只允许执行路径显式解析，列表和详情对象都不得持有明文或密文。
    provider = repository.get("deepseek")
    assert repository.resolve_api_key("deepseek") == "sk-first"
    assert provider.name == "DeepSeek"
    assert provider.base_url == "https://gateway.example.com/v1"
    assert provider.key_hint == "irst"
    assert provider.key_configured is True


def test_database_persists_ciphertext_and_builtin_delete_resets_override(
    repository: ModelProviderRepository,
) -> None:
    """数据库只能保存密文，删除内置覆盖后应恢复默认地址并清除凭据。"""
    repository.save(
        "siliconflow",
        name="硅基流动",
        base_url="https://proxy.example.com/v1",
        api_key="sk-database-secret",
    )

    # 直接读取底层数据库用于确认持久化值不是浏览器提交的完整 API Key。
    connection = sqlite3.connect(repository.database_path)
    encrypted_value = connection.execute(
        "SELECT encrypted_api_key FROM model_providers WHERE id = 'siliconflow'"
    ).fetchone()[0]
    connection.close()
    assert "sk-database-secret" not in encrypted_value

    repository.delete("siliconflow")
    restored = repository.get("siliconflow")
    assert restored.base_url == "https://api.siliconflow.cn/v1"
    assert restored.key_configured is False
    with pytest.raises(ModelProviderCredentialError, match="API Key"):
        repository.resolve_api_key("siliconflow")


def test_custom_provider_round_trips_and_can_be_deleted(
    repository: ModelProviderRepository,
) -> None:
    """自定义服务商应使用生成标识持久化，并可被完整删除。"""
    created = repository.save(
        None,
        name="Internal Gateway",
        base_url="http://127.0.0.1:9000/v1/",
        api_key="local-token",
    )

    # 自定义记录排在三个内置项之后，重新打开仓储仍可正确解密凭据。
    assert created.id.startswith("provider_")
    assert created.kind == "custom"
    restored_repository = ModelProviderRepository(repository.database_path, repository.cipher)
    assert restored_repository.resolve_api_key(created.id) == "local-token"
    assert restored_repository.list()[-1].id == created.id

    restored_repository.delete(created.id)
    with pytest.raises(ModelProviderNotFoundError, match=created.id):
        restored_repository.get(created.id)


@pytest.mark.parametrize(
    "value",
    [
        "http://api.example.com/v1",
        "ftp://api.example.com/v1",
        "https://user:password@example.com/v1",
        "https://api.example.com/v1?token=secret",
        "https://api.example.com/v1#models",
        "not-a-url",
    ],
)
def test_normalize_provider_base_url_rejects_unsafe_values(value: str) -> None:
    """服务地址必须使用受支持协议且不能夹带凭据、查询参数或片段。"""
    with pytest.raises(ValueError, match="Base URL"):
        normalize_provider_base_url(value)


def test_normalize_provider_base_url_allows_https_and_loopback_http() -> None:
    """远程 HTTPS 与本机 HTTP 应规范化尾斜杠并保留版本路径。"""
    assert normalize_provider_base_url(" HTTPS://API.EXAMPLE.COM/v1/ ") == (
        "https://api.example.com/v1"
    )
    assert normalize_provider_base_url("http://[::1]:8080/v1/") == "http://[::1]:8080/v1"
