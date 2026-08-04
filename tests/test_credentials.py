"""验证模型服务商凭据只以认证密文落盘，并严格保护本地主密钥。"""

import stat
from pathlib import Path

import pytest

from evalhub.credentials import CredentialCipher, CredentialConfigurationError


def test_generated_key_file_encrypts_without_plaintext(tmp_path: Path) -> None:
    """自动生成的本地主密钥应限制权限，并能完成不含明文的加解密往返。

    Args:
        tmp_path: pytest 提供的隔离运行目录，避免测试接触真实 ``.runtime``。
    """
    cipher = CredentialCipher.from_runtime(tmp_path, env={})
    token = cipher.encrypt("sk-secret-value")

    # 数据库将保存字符串密文，因此同时保护字符串表示和解密后的原始值。
    assert "sk-secret-value" not in token
    assert cipher.decrypt(token) == "sk-secret-value"
    key_path = tmp_path / "provider_credentials.key"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_invalid_environment_key_fails_closed(tmp_path: Path) -> None:
    """非法环境主密钥必须阻止凭据功能，不能静默生成另一把密钥。

    Args:
        tmp_path: 用于确认失败路径没有创建回退密钥文件的隔离目录。
    """
    with pytest.raises(CredentialConfigurationError, match="EVALHUB_CREDENTIAL_KEY"):
        CredentialCipher.from_runtime(
            tmp_path,
            env={"EVALHUB_CREDENTIAL_KEY": "invalid"},
        )

    # 环境变量具有明确优先级，配置错误时不得留下让后续重启产生歧义的文件。
    assert not (tmp_path / "provider_credentials.key").exists()
