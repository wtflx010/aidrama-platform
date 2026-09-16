"""简易 JSONPath 取值，支持 'a||b' fallback。基于 jsonpath_ng。"""
from jsonpath_ng import parse as _jp_parse


def jsonpath_get(data, expr: str):
    """按 expr 取值；expr 可含 '||' 表示依次尝试，返回首个非空。"""
    if not expr:
        return None
    for branch in str(expr).split("||"):
        branch = branch.strip()
        if not branch:
            continue
        try:
            for match in _jp_parse(branch).find(data):
                if match.value is not None:
                    return match.value
        except Exception:
            continue
    return None
