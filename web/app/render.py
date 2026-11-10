from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def label_font():
    from PIL import ImageFont
    mac_font = Path("/System/Library/Fonts/AppleSDGothicNeo.ttc")
    return ImageFont.truetype(str(mac_font), 20) if mac_font.exists() else ImageFont.load_default()


def encode_jpeg(frame, objects, quality):
    import cv2
    image = frame.copy()
    unicode_labels = []
    for obj in objects:
        x1, y1, x2, y2 = (int(v) for v in obj["xyxy"])
        color = (102, 204, 255) if obj.get("collection_candidate") else (65, 220, 170)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 3 if obj.get("collection_candidate") else 2)
        label = f'{obj["label"]} {obj["confidence"]:.2f}'
        if label.isascii():
            cv2.putText(image, label, (x1, max(18, y1 - 7)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, color, 2, cv2.LINE_AA)
        else:
            unicode_labels.append((label, (x1, max(0, y1-25)), color[::-1]))
    if unicode_labels:
        import numpy as np
        from PIL import Image, ImageDraw
        pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(pil)
        for label, position, color in unicode_labels:
            draw.text(position, label, font=label_font(), fill=color)
        image = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG 인코딩에 실패했습니다.")
    return encoded.tobytes()
