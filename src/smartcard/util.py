def toHexString(data=None, format=0):
    if not data:
        return ""
    return " ".join(f"{byte:02X}" for byte in data)


def toBytes(text):
    return [int(part, 16) for part in text.replace(":", " ").split()]
