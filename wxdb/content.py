"""Conservative parsing of message XML; original message_content is never changed."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET


def _integer(value: str | None) -> int | None:
    if value is None or not re.fullmatch(r"\d{1,19}", value):
        return None
    return int(value)


def parse_message_content(content: str, local_type: int) -> dict:
    base_type = local_type & 0xFFFFFFFF
    kind = {1: "text", 3: "image", 34: "voice", 49: "app"}.get(base_type, f"type_{base_type}")
    result = {"message_type": kind, "message_text": content, "content_parse_status": "not_applicable"}
    if base_type not in (3, 34, 49):
        return result
    result["message_text"] = {3: "[图片]", 34: "[语音]", 49: "[应用消息]"}[base_type]
    result["content_parse_status"] = "empty" if not content else "unsupported"
    if not content:
        return result
    xml = content.strip()
    # Group messages may prefix the XML with the sender's username.
    prefix = re.match(r"^([^\s<>:]+):\r?\n(?=<)", xml)
    if prefix:
        result["content_sender_username"] = prefix.group(1)
        xml = xml[prefix.end():]
    if len(xml.encode("utf-8")) > 16 * 1024 * 1024:
        result["content_parse_status"] = "too_large"
        return result
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.I):
        result["content_parse_status"] = "unsafe_xml"
        return result
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        result["content_parse_status"] = "invalid_xml"
        return result
    # Only direct, known envelopes: a nested quoted image is not the current image.
    def node(name: str) -> ET.Element | None:
        return root if root.tag == name else root.find(name) if root.tag == "msg" else None

    if base_type == 3:
        image = node("img")
        if image is None:
            return result
        attrs = image.attrib
        metadata = {}
        for name in ("md5", "length", "hdlength", "width", "height", "cdnthumbwidth", "cdnthumbheight",
                     "cdnmidwidth", "cdnmidheight", "tpthumbwidth", "tpthumbheight"):
            if name in attrs:
                metadata[name] = attrs[name] if name == "md5" else _integer(attrs[name])
        # CDN parameters are not local filenames and are never downloaded here.
        metadata["has_cdn_reference"] = any(attrs.get(k) for k in ("cdnmidimgurl", "cdnbigimgurl", "cdnthumburl"))
        result["image_metadata"] = metadata
    elif base_type == 34:
        voice = node("voicemsg")
        if voice is None:
            return result
        attrs = voice.attrib
        metadata = {"duration_ms": _integer(attrs.get("voicelength")),
                    "format_code": _integer(attrs.get("voiceformat")),
                    "byte_length": _integer(attrs.get("length")),
                    "has_remote_url": bool(attrs.get("voiceurl"))}
        for key in ("endflag", "cancelflag", "forwardflag"):
            if key in attrs:
                metadata[key] = _integer(attrs[key])
        result["voice"] = metadata
        if metadata["duration_ms"] is not None:
            result["message_text"] = f"[语音 {metadata['duration_ms'] / 1000:g} 秒]"
    else:
        app = node("appmsg")
        if app is None:
            return result
        subtype = _integer(app.findtext("type"))
        result["app_message_type"] = subtype
        if subtype != 57:
            # Do not conflate other application payloads with quoted replies.
            result["message_text"] = app.findtext("title") or "[应用消息]"
            return result
        result["message_type"] = "quote"
        title = app.findtext("title") or ""
        result["reply_text"] = title
        result["message_text"] = title or "[引用回复]"
        reference = app.find("refermsg")
        if reference is None:
            result["content_parse_status"] = "missing_reference"
            return result
        result["reference"] = {
            "type": _integer(reference.findtext("type")),
            # Preserve 64-bit IDs as strings for JSON/JavaScript consumers.
            "server_id": reference.findtext("svrid") or "",
            "sender_username": reference.findtext("fromusr") or "",
            "chat_username": reference.findtext("chatusr") or "",
            "display_name": reference.findtext("displayname") or "",
            "content": reference.findtext("content") or "",
        }
        ref = result["reference"]
        preview = ref["content"] if ref["type"] == 1 else {
            3: "[图片]", 34: "[语音]", 43: "[视频]",
        }.get(ref["type"], "[非文本消息]")
        result["message_text"] += f"\n[引用 {ref['display_name']}: {preview}]"
    result["content_parse_status"] = "ok"
    return result
