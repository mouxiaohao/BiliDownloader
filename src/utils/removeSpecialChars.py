import re


def removeSpecialChars(original: str, limit=None):
    """Keep full titles by default; an explicit limit is honored by callers."""
    spe = ":~!?@#$%^&*()+*/<>,.[]\\|\"' "
    ret = re.sub(r"[\x00-\x1f\x7f]", "_", original)
    for ch in spe:
        ret = ret.replace(ch, "_")
    if limit:
        if len(ret) > limit:
            ret = ret[:limit]
    if len(ret) == 0:
        ret = "_"
    return ret
