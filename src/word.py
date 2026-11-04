"""Extract searchable body text from a bounded DOCX package."""
import base64
import binascii
import io
import zipfile
import xml.etree.ElementTree as ET

MAX_FILE = 4 * 1024 * 1024
MAX_XML = 10 * 1024 * 1024
MAX_TEXT = 200000
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def extract_docx(encoded):
    if not isinstance(encoded, str) or len(encoded) > ((MAX_FILE + 2) // 3) * 4:
        raise ValueError('Word 文件超过 4 MB 限制')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('文件编码无效') from None
    if not raw or len(raw) > MAX_FILE:
        raise ValueError('Word 文件为空或超过 4 MB 限制')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = [x for x in archive.infolist() if x.filename == 'word/document.xml']
            if len(files) != 1:
                raise ValueError('Word 文件缺少有效正文或包含重复正文')
            info = files[0]
            if info.flag_bits & 1 or info.file_size > MAX_XML:
                raise ValueError('加密文件或过大的 Word 正文不受支持')
            xml = archive.read(info)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError):
        raise ValueError('无法读取 DOCX，请检查文件是否损坏或加密') from None
    if b'\x00' in xml or b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
        raise ValueError('Word 正文包含不支持的 XML 声明')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise ValueError('Word 正文格式无效') from None
    body = root.find(W + 'body')
    if body is None:
        raise ValueError('Word 文件没有正文')
    paragraphs = []
    for paragraph in body.iter(W + 'p'):
        pieces = []
        for node in paragraph.iter():
            if node.tag == W + 't': pieces.append(node.text or '')
            elif node.tag == W + 'tab': pieces.append('\t')
            elif node.tag in (W + 'br', W + 'cr'): pieces.append('\n')
        text = ''.join(pieces).strip()
        if text: paragraphs.append(text)
    text = '\n'.join(paragraphs)
    if not text:
        raise ValueError('未提取到正文文字；图片和扫描件暂不支持识别')
    if len(text) > MAX_TEXT:
        raise ValueError('提取的正文超过 200000 字符限制')
    return text
