"""密钥解析：api_key_ref 是环境变量名，运行时取其值，DB 不存明文。"""
import os


def resolve_key(api_key_ref: str | None) -> str:
    if not api_key_ref:
        return ""
    return os.environ.get(api_key_ref, "")
