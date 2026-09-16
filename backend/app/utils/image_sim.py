"""图像相似度工具：感知哈希（pHash），用于检测四视图等生成结果是否与参考图雷同。"""
from PIL import Image


def phash(path: str, size: int = 16) -> str:
    """计算图片感知哈希（灰度缩略图均值哈希）。

    - size=16 → 256 位哈希，对构图/布局差异敏感（对纯色背景不敏感）
    - 返回值：'0'/'1' 组成的字符串，长度 = size*size
    """
    im = Image.open(path).convert("L").resize((size, size))
    px = im.tobytes()  # 灰度模式 1 byte/像素，避免 getdata 弃用警告
    avg = sum(px) / len(px)
    return "".join("1" if v > avg else "0" for v in px)


def hamming(a: str, b: str) -> int:
    """两个同长度哈希的汉明距离（不同的位数）。"""
    return sum(1 for x, y in zip(a, b) if x != y)
