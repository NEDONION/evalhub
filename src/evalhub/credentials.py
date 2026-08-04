"""为模型服务商 API Key 提供认证加密和本地主密钥生命周期管理。"""

import binascii
import os
from collections.abc import Mapping
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_CREDENTIAL_KEY_ENV = "EVALHUB_CREDENTIAL_KEY"
_KEY_FILE_NAME = "provider_credentials.key"


class CredentialConfigurationError(RuntimeError):
    """表示主密钥来源、格式或文件权限不满足安全要求。"""


class CredentialDecryptionError(RuntimeError):
    """表示密文无法由当前主密钥完成认证解密。"""


class CredentialCipher:
    """使用 Fernet 对服务商凭据执行带完整性校验的字符串加解密。"""

    def __init__(self, fernet: Fernet) -> None:
        """保存已经完成格式验证的 Fernet 实例。

        Args:
            fernet: 使用环境变量或本地权限文件构造的认证加密器。
        """
        self._fernet = fernet

    @classmethod
    def from_runtime(
        cls,
        runtime_dir: Path,
        env: Mapping[str, str] | None = None,
    ) -> "CredentialCipher":
        """从环境变量或运行目录创建凭据加密器。

        Args:
            runtime_dir: 存放自动生成主密钥的项目运行目录。
            env: 可注入的环境变量映射；省略时读取当前进程环境。

        Returns:
            使用已验证主密钥构造的凭据加密器。

        Raises:
            CredentialConfigurationError: 环境密钥非法，或本地密钥文件不安全时抛出。
            OSError: 运行目录或主密钥文件无法创建、读取或写入时抛出。
        """
        environment = os.environ if env is None else env
        configured_key = environment.get(_CREDENTIAL_KEY_ENV)

        # 显式环境变量始终优先；配置错误时禁止回退，以免同一数据库出现两把主密钥。
        if configured_key is not None:
            return cls(_validated_fernet(configured_key, _CREDENTIAL_KEY_ENV))

        runtime_dir.mkdir(parents=True, exist_ok=True)
        key_path = runtime_dir / _KEY_FILE_NAME
        key = _read_or_create_key(key_path)
        return cls(_validated_fernet(key, str(key_path)))

    def encrypt(self, value: str) -> str:
        """把非空明文编码为可写入 SQLite 的 Fernet 字符串。

        Args:
            value: 需要保护的完整 API Key。

        Returns:
            包含随机数和认证标签的 URL-safe Base64 密文。

        Raises:
            ValueError: 调用方尝试加密空凭据时抛出。
        """
        if not value:
            raise ValueError("credential must not be empty")
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """认证并解密 SQLite 中保存的 Fernet 字符串。

        Args:
            token: 仓储读取到的完整密文字符串。

        Returns:
            仅供当前模型请求短暂使用的 API Key 明文。

        Raises:
            CredentialDecryptionError: 密文损坏或主密钥不匹配时抛出。
        """
        try:
            plaintext = self._fernet.decrypt(token.encode("ascii"))
        except (InvalidToken, UnicodeEncodeError) as exc:
            # 错误消息不携带密文或主密钥路径，只保留异常链供本地调试定位。
            raise CredentialDecryptionError("模型服务商凭据无法解密") from exc
        return plaintext.decode("utf-8")


def _validated_fernet(key: str, source: str) -> Fernet:
    """验证主密钥格式并转换为 Fernet 实例。

    Args:
        key: 待校验的 URL-safe Base64 Fernet key。
        source: 用于错误定位的环境变量名或文件路径。

    Returns:
        可直接执行认证加密的 Fernet 实例。

    Raises:
        CredentialConfigurationError: 主密钥不是合法 Fernet key 时抛出。
    """
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
        # 只标识配置来源，不把非法密钥本身拼入诊断消息。
        raise CredentialConfigurationError(f"无效的凭据主密钥：{source}") from exc


def _read_or_create_key(key_path: Path) -> str:
    """读取安全的现有主密钥，或以独占方式创建权限为 0600 的新文件。

    Args:
        key_path: 项目运行目录中的固定主密钥文件位置。

    Returns:
        文件内不含换行的 Fernet key 字符串。

    Raises:
        CredentialConfigurationError: 路径类型或现有文件权限不安全时抛出。
        OSError: 文件系统创建、读取或写入失败时抛出。
    """
    if key_path.exists() or key_path.is_symlink():
        return _read_existing_key(key_path)

    generated = Fernet.generate_key().decode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(key_path, flags, 0o600)
    except FileExistsError:
        # 另一线程刚完成创建时重新走同一权限校验，避免竞态覆盖主密钥。
        return _read_existing_key(key_path)

    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as file:
            file.write(generated)
    except OSError:
        # 只清理由当前独占创建但未写完的文件，保留原始文件系统异常上下文。
        key_path.unlink(missing_ok=True)
        raise
    return generated


def _read_existing_key(key_path: Path) -> str:
    """校验现有主密钥文件类型和权限后读取内容。

    Args:
        key_path: 已存在或疑似为符号链接的主密钥路径。

    Returns:
        去除文件末尾空白后的主密钥字符串。

    Raises:
        CredentialConfigurationError: 文件是链接、非普通文件或权限过宽时抛出。
        OSError: 文件元数据或内容无法读取时抛出。
    """
    if key_path.is_symlink() or not key_path.is_file():
        raise CredentialConfigurationError("凭据主密钥路径必须是普通文件")

    permissions = key_path.stat().st_mode & 0o777
    if permissions & 0o077:
        raise CredentialConfigurationError("凭据主密钥文件权限必须为 0600 或更严格")
    return key_path.read_text(encoding="ascii").strip()
